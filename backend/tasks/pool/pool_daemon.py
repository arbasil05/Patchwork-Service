import sys
import os
import signal
import time
import logging
from concurrent.futures import ThreadPoolExecutor
import docker
import redis

_file_path = os.path.abspath(__file__.rstrip("\\/"))
_current_dir = os.path.dirname(_file_path)
_backend_root = os.path.join(_current_dir, "..", "..")

# Ensure the backend root is on the Python path to import config and storage
sys.path.insert(0, _backend_root)
# Ensure the current directory is on the Python path to import pool_manager when run with trailing slashes
sys.path.insert(0, _current_dir)

from pool_manager import provision_new_container, _get_idle_key, _get_busy_key, _get_pending_key
from config.settings import settings
from storage.pool_defs import seed_default_pools, list_pool_defs

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

logger = logging.getLogger("pool_daemon")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)

redis_client = redis.Redis(host=settings.REDIS_HOST, port=6379, decode_responses=True)
docker_client = docker.from_env()

# Read supported images from centralized settings
SUPPORTED_IMAGES = [t.strip() for t in settings.SUPPORTED_IMAGES.split(",") if t.strip()]

# Global fallback defaults
DEFAULT_FALLBACK_BUFFER = int(os.getenv("TARGET_BUFFER", 12))
DEFAULT_FALLBACK_MAX = int(os.getenv("MAX_POOL_SIZE", 20))
CHECK_INTERVAL = float(os.getenv("CHECK_INTERVAL", 2))
PROVISION_CONCURRENCY = int(os.getenv("PROVISION_CONCURRENCY", 5))
SINGLE_DAEMON_LOCK_KEY = "lock:pool_daemon"
SINGLE_DAEMON_LOCK_TTL = 15  # seconds

# Framework default targets if not explicitly set in pool definition
FRAMEWORK_DEFAULTS = {
    "patchwork-django": {"target_buffer": 12, "max_containers": 20},
    # "patchwork-express": {"target_buffer": 12, "max_containers": 20},
}

running = True
shutdown_requested = False
executor: ThreadPoolExecutor = None
active_futures = set()

# Lua scripts for atomic pending counter management and slot reservation
RESERVE_SLOTS_LUA = """
local idle_key = KEYS[1]
local busy_key = KEYS[2]
local pending_key = KEYS[3]
local target_idle = tonumber(ARGV[1])
local max_containers = tonumber(ARGV[2])
local max_batch = tonumber(ARGV[3])

local actual_idle = tonumber(redis.call('SCARD', idle_key) or 0)
local actual_busy = tonumber(redis.call('SCARD', busy_key) or 0)
local raw_pending = redis.call('GET', pending_key)
local pending = tonumber(raw_pending or 0)
if pending < 0 then
    pending = 0
    redis.call('SET', pending_key, 0)
end

local eff_idle = actual_idle + pending
local eff_total = actual_idle + actual_busy + pending

local needed = target_idle - eff_idle
if needed <= 0 then
    return {0, pending, actual_idle, actual_busy}
end

local avail = max_containers - eff_total
if avail <= 0 then
    return {0, pending, actual_idle, actual_busy}
end

local to_spawn = math.min(needed, avail)
if max_batch > 0 and to_spawn > max_batch then
    to_spawn = max_batch
end

if to_spawn > 0 then
    pending = redis.call('INCRBY', pending_key, to_spawn)
end

return {to_spawn, pending, actual_idle, actual_busy}
"""

RELEASE_PENDING_LUA = """
local pending_key = KEYS[1]
local count = tonumber(ARGV[1] or 1)
local val = redis.call('DECRBY', pending_key, count)
if val <= 0 then
    redis.call('SET', pending_key, 0)
    return 0
end
return val
"""

_reserve_script = None
_release_script = None


def get_reserve_script():
    global _reserve_script
    if _reserve_script is None:
        _reserve_script = redis_client.register_script(RESERVE_SLOTS_LUA)
    return _reserve_script


def get_release_script():
    global _release_script
    if _release_script is None:
        _release_script = redis_client.register_script(RELEASE_PENDING_LUA)
    return _release_script


def decrement_pending(image_tag: str, count: int = 1):
    try:
        script = get_release_script()
        return script(keys=[_get_pending_key(image_tag)], args=[count])
    except Exception as e:
        logger.error(f"[Daemon][{image_tag}] Error decrementing pending counter: {e}")
        try:
            val = redis_client.decrby(_get_pending_key(image_tag), count)
            if val < 0:
                redis_client.set(_get_pending_key(image_tag), 0)
                return 0
            return val
        except Exception:
            return 0


def get_pending_count(image_tag: str) -> int:
    try:
        val = redis_client.get(_get_pending_key(image_tag))
        if val is None:
            return 0
        ival = int(val)
        return max(0, ival)
    except Exception:
        return 0


def log_provision_duration(image_tag: str, t_start: float, duration: float):
    try:
        csv_dir = os.path.abspath(os.path.join(_backend_root, "..", "eval", "results"))
        os.makedirs(csv_dir, exist_ok=True)
        csv_path = os.path.join(csv_dir, "daemon_provision_times.csv")
        header_needed = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
        with open(csv_path, "a", encoding="utf-8") as f:
            if header_needed:
                f.write("timestamp,image_tag,seconds\n")
            f.write(f"{t_start},{image_tag},{duration:.6f}\n")
    except Exception as e:
        logger.warning(f"[Daemon][{image_tag}] Error appending to provision CSV: {e}")


def _provision_task(image_tag: str):
    """Background provisioning worker task executed in ThreadPoolExecutor."""
    t_start = time.time()
    container_id = None
    try:
        if shutdown_requested:
            logger.info(f"[Daemon][{image_tag}] Aborting provisioning task due to shutdown.")
            return None

        logger.info(f"[Daemon][{image_tag}] Provisioning task started...")
        container_id = provision_new_container(image_tag)
        duration = time.time() - t_start
        log_provision_duration(image_tag, t_start, duration)
        logger.info(f"[Daemon][{image_tag}] Provisioned {container_id} in {duration:.3f}s")
        return container_id
    except Exception as e:
        duration = time.time() - t_start
        logger.error(f"[Daemon][{image_tag}] Provisioning task failed after {duration:.3f}s: {e}")
        return None
    finally:
        decrement_pending(image_tag, 1)


def get_effective_pool_config(pool: dict):
    """
    Determine the effective target_buffer and max_containers for a pool definition.
    Precedence:
    1. Pool definition stored value if valid and positive (>0)
    2. Framework-specific defaults (Django: B=8, M=15; Express: B=3, M=15)
    3. Global environment fallbacks (TARGET_BUFFER, MAX_POOL_SIZE)
    """
    pool_name = pool.get("name", "")
    image_tag = pool.get("image_tag", "")
    fw_cfg = FRAMEWORK_DEFAULTS.get(pool_name, {})
    if not fw_cfg:
        for k, v in FRAMEWORK_DEFAULTS.items():
            if k in pool_name or k in image_tag:
                fw_cfg = v
                break

    # 1. max_containers
    raw_max = pool.get("max_containers")
    try:
        if raw_max is not None and str(raw_max).strip() != "":
            parsed_max = int(raw_max)
            if parsed_max >= 0:
                max_containers = parsed_max
            else:
                max_containers = fw_cfg.get("max_containers", DEFAULT_FALLBACK_MAX)
        else:
            max_containers = fw_cfg.get("max_containers", DEFAULT_FALLBACK_MAX)
    except (ValueError, TypeError):
        max_containers = fw_cfg.get("max_containers", DEFAULT_FALLBACK_MAX)

    # 2. target_buffer
    raw_tb = pool.get("target_buffer")
    try:
        if raw_tb is not None and str(raw_tb).strip() != "":
            parsed_tb = int(raw_tb)
            if parsed_tb >= 0:
                target_buffer = parsed_tb
            else:
                target_buffer = fw_cfg.get("target_buffer", DEFAULT_FALLBACK_BUFFER)
        else:
            target_buffer = fw_cfg.get("target_buffer", DEFAULT_FALLBACK_BUFFER)
    except (ValueError, TypeError):
        target_buffer = fw_cfg.get("target_buffer", DEFAULT_FALLBACK_BUFFER)

    # Target buffer cannot exceed max_containers
    effective_target = min(target_buffer, max_containers)
    return effective_target, max_containers


def get_pool_metrics(image_tag: str):
    """Returns actual idle count and busy count from Redis."""
    try:
        idle_count = redis_client.scard(_get_idle_key(image_tag))
    except Exception:
        idle_count = 0
    try:
        busy_count = redis_client.scard(_get_busy_key(image_tag))
    except Exception:
        busy_count = 0
    return idle_count, busy_count


def scale_down_pool(image_tag: str, idle_count: int, busy_count: int, target_buffer: int, max_containers: int):
    """
    Scale down excess idle containers while protecting busy containers.
    Rule 1 (Hard ceiling): If total containers (idle + busy) exceed max_containers, trim idle.
    Rule 2 (Buffer trim): If idle exceeds target_buffer, trim idle down to target_buffer.
    Returns number of containers reaped.
    """
    reaped_total = 0
    total_containers = idle_count + busy_count
    idle_key = _get_idle_key(image_tag)

    # 1. Ceiling scale-down rule: If total exceeds max_containers, reap idle containers (never busy)
    if total_containers > max_containers and idle_count > 0:
        excess_total = total_containers - max_containers
        to_reap = min(excess_total, idle_count)
        logger.info(f"[Daemon][{image_tag}] Total ({total_containers}) exceeds ceiling ({max_containers}). Draining {to_reap} idle container(s)...")

        for _ in range(to_reap):
            container_id = redis_client.spop(idle_key)
            if not container_id:
                break
            try:
                container = docker_client.containers.get(container_id)
                container.kill()
                logger.info(f"[Daemon][{image_tag}] Reaped (ceiling): {container_id}")
                reaped_total += 1
            except docker.errors.NotFound:
                reaped_total += 1
            except Exception as e:
                logger.error(f"[Daemon][{image_tag}] Error tearing down {container_id}: {e}")

            idle_count -= 1
            total_containers -= 1

    # 2. Buffer scale-down rule: Idle-only trim when idle exceeds buffer target
    target_idle = min(target_buffer, max_containers)
    if idle_count > target_idle:
        excess = idle_count - target_idle
        logger.info(f"[Daemon][{image_tag}] Idle trim: Idle {idle_count} -> {target_idle}. Reaping {excess} container(s)...")

        for _ in range(excess):
            container_id = redis_client.spop(idle_key)
            if not container_id:
                break
            try:
                container = docker_client.containers.get(container_id)
                container.kill()
                logger.info(f"[Daemon][{image_tag}] Reaped (buffer): {container_id}")
                reaped_total += 1
            except docker.errors.NotFound:
                reaped_total += 1
            except Exception as e:
                logger.error(f"[Daemon][{image_tag}] Error tearing down {container_id}: {e}")

            idle_count -= 1

    return reaped_total


def scale_up_pool(image_tag: str, target_idle: int, max_containers: int):
    """
    Scale up pool towards target_idle without exceeding max_containers.
    Uses atomic Redis reservation to count pending provisions.
    Submits background provisioning tasks to executor without blocking daemon loop.
    """
    global executor, active_futures

    if shutdown_requested or not running:
        return 0

    if target_idle <= 0 or max_containers <= 0:
        return 0

    idle_key = _get_idle_key(image_tag)
    busy_key = _get_busy_key(image_tag)
    pending_key = _get_pending_key(image_tag)

    # Clean up completed futures from active tracking set in-place
    active_futures.difference_update({f for f in list(active_futures) if f.done()})

    # Calculate remaining capacity in ThreadPoolExecutor
    avail_worker_slots = max(0, PROVISION_CONCURRENCY - len(active_futures))
    if avail_worker_slots <= 0:
        pending_count = get_pending_count(image_tag)
        idle_count, busy_count = get_pool_metrics(image_tag)
        logger.debug(f"[Daemon][{image_tag}] Provisioning concurrency limit reached ({len(active_futures)} active). Pending={pending_count}, Idle={idle_count}, Busy={busy_count}")
        return 0

    try:
        script = get_reserve_script()
        res = script(
            keys=[idle_key, busy_key, pending_key],
            args=[target_idle, max_containers, avail_worker_slots]
        )
        to_spawn = int(res[0])
        current_pending = int(res[1])
        actual_idle = int(res[2])
        actual_busy = int(res[3])
    except Exception as e:
        logger.error(f"[Daemon][{image_tag}] Error executing reservation Lua script: {e}")
        return 0

    if to_spawn <= 0:
        return 0

    logger.info(f"[Daemon][{image_tag}] Pool capacity needed! Idle: {actual_idle}/{target_idle}, Busy: {actual_busy}, Pending: {current_pending}, Max: {max_containers}. Reserved & scheduling {to_spawn} background provision(s)...")

    scheduled = 0
    for _ in range(to_spawn):
        try:
            future = executor.submit(_provision_task, image_tag)
            active_futures.add(future)
            scheduled += 1
        except Exception as e:
            logger.error(f"[Daemon][{image_tag}] Failed to submit provisioning task to executor: {e}")
            decrement_pending(image_tag, 1)

    return scheduled


def reconcile_orphans(image_tag: str):
    """Reconcile orphaned Docker containers and stale Redis IDs."""
    idle_key = _get_idle_key(image_tag)
    busy_key = _get_busy_key(image_tag)
    try:
        known_ids = redis_client.smembers(idle_key) | redis_client.smembers(busy_key)
    except Exception as e:
        logger.error(f"[Daemon][{image_tag}] Redis error reading known IDs: {e}")
        return

    sanitized_tag = image_tag.replace(":", "_").replace("/", "_")
    try:
        all_runner_containers = docker_client.containers.list(filters={"name": f"{sanitized_tag}_"})
    except Exception as e:
        logger.error(f"[Daemon][{image_tag}] Docker error listing containers: {e}")
        return

    running_names = {c.name for c in all_runner_containers}

    orphans = [c for c in all_runner_containers if c.name not in known_ids and not c.name.startswith("ondemand_")]
    if orphans:
        logger.info(f"[Daemon][{image_tag}] Found {len(orphans)} orphaned container(s). Reaping...")
        for c in orphans:
            try:
                c.kill()
                logger.info(f"  ✓ Reaped orphan: {c.name}")
            except Exception as e:
                logger.error(f"  ✗ Failed to reap {c.name}: {e}")

    stale_ids = known_ids - running_names
    if stale_ids:
        logger.info(f"[Daemon][{image_tag}] Found {len(stale_ids)} stale Redis entries. Cleaning...")
        for stale_id in stale_ids:
            try:
                redis_client.srem(idle_key, stale_id)
                redis_client.srem(busy_key, stale_id)
                logger.info(f"  ✓ Removed stale: {stale_id}")
            except Exception as e:
                logger.error(f"  ✗ Error removing stale {stale_id}: {e}")


def acquire_daemon_lock():
    """Acquire single-instance daemon lock in Redis with TTL."""
    try:
        acquired = redis_client.set(SINGLE_DAEMON_LOCK_KEY, str(os.getpid()), nx=True, ex=SINGLE_DAEMON_LOCK_TTL)
        return bool(acquired)
    except Exception as e:
        logger.warning(f"[Daemon] Warning: Could not check/set daemon lock in Redis: {e}")
        return True


def refresh_daemon_lock():
    """Renew single-instance daemon lock TTL."""
    try:
        current_val = redis_client.get(SINGLE_DAEMON_LOCK_KEY)
        if current_val == str(os.getpid()):
            redis_client.expire(SINGLE_DAEMON_LOCK_KEY, SINGLE_DAEMON_LOCK_TTL)
    except Exception:
        pass


def release_daemon_lock():
    """Release single-instance daemon lock in Redis."""
    try:
        current_val = redis_client.get(SINGLE_DAEMON_LOCK_KEY)
        if current_val == str(os.getpid()):
            redis_client.delete(SINGLE_DAEMON_LOCK_KEY)
    except Exception:
        pass


def handle_shutdown(signum=None, frame=None):
    """Coordinated graceful shutdown handler."""
    global running, shutdown_requested
    if shutdown_requested:
        return
    shutdown_requested = True
    running = False
    logger.info("\n[Daemon] Shutdown initiated — stopping provisioning and cleaning up pooled containers...")


def execute_shutdown_cleanup():
    """Perform final cleanup of in-flight tasks, pending counters, pooled containers, and lock."""
    global executor, active_futures

    if executor:
        logger.info(f"[Daemon] Shutting down thread pool executor (waiting up to 10s for {len(active_futures)} active tasks)...")
        executor.shutdown(wait=True, cancel_futures=True)

    # Collect all image tags from active pool records plus SUPPORTED_IMAGES
    all_tags = set(SUPPORTED_IMAGES)
    try:
        pools = list_pool_defs()
        for p in pools:
            if p and p.get("image_tag"):
                all_tags.add(p["image_tag"])
    except Exception:
        pass

    for image_tag in all_tags:
        idle_key = _get_idle_key(image_tag)
        busy_key = _get_busy_key(image_tag)
        pending_key = _get_pending_key(image_tag)

        # Clear pending counters
        try:
            redis_client.delete(pending_key)
        except Exception:
            pass

        try:
            all_ids = redis_client.smembers(idle_key) | redis_client.smembers(busy_key)
        except Exception:
            all_ids = set()

        if not all_ids:
            logger.info(f"[Daemon][{image_tag}] No containers to clean up.")
        else:
            logger.info(f"[Daemon][{image_tag}] Killing {len(all_ids)} container(s)...")
            for container_id in all_ids:
                try:
                    container = docker_client.containers.get(container_id)
                    container.kill()
                    logger.info(f"  ✓ Killed {container_id}")
                except docker.errors.NotFound:
                    logger.info(f"  ✗ {container_id} already gone")
                except Exception as e:
                    logger.error(f"  ✗ Error killing {container_id}: {e}")

        try:
            redis_client.delete(idle_key, busy_key)
            logger.info(f"[Daemon][{image_tag}] Redis keys flushed.")
        except Exception as e:
            logger.error(f"[Daemon][{image_tag}] Error flushing Redis keys: {e}")

    release_daemon_lock()
    logger.info("[Daemon] Shutdown complete.")


def run_daemon():
    global executor, running

    logger.info("[Daemon] Dynamic pool management daemon starting...")
    logger.info(f"[Daemon] Global environment fallback defaults: TARGET_BUFFER={DEFAULT_FALLBACK_BUFFER}, MAX_POOL_SIZE={DEFAULT_FALLBACK_MAX}")
    logger.info(f"[Daemon] Provisioning concurrency: {PROVISION_CONCURRENCY}, Check interval: {CHECK_INTERVAL}s")
    logger.info(f"[Daemon] Supported default images: {', '.join(SUPPORTED_IMAGES)}")

    # Enforce single-daemon instance deployment design
    if not acquire_daemon_lock():
        existing_pid = redis_client.get(SINGLE_DAEMON_LOCK_KEY)
        logger.error(f"[Daemon] Another pool daemon instance is already active (PID: {existing_pid}, Lock: {SINGLE_DAEMON_LOCK_KEY}). Exiting.")
        sys.exit(1)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Initial seeding and synchronization of default pools (migrates 10 -> 15 and sets targets)
    try:
        seed_default_pools(force_sync_defaults=True)
    except Exception as e:
        logger.warning(f"[Daemon] Warning: Failed to seed default pools on startup: {e}")

    # Log initial effective runtime configuration for each pool
    try:
        current_pools = list_pool_defs()
        for p in current_pools:
            if not p:
                continue
            eff_tb, eff_max = get_effective_pool_config(p)
            idle, busy = get_pool_metrics(p["image_tag"])
            pending = get_pending_count(p["image_tag"])
            logger.info(f"[Daemon][Startup Config] Pool '{p['name']}' ({p['image_tag']}): Target Warm Buffer = {eff_tb}, Max Containers = {eff_max} | Current: Idle={idle}, Busy={busy}, Pending={pending}")
    except Exception as e:
        logger.warning(f"[Daemon] Warning: Could not log initial pool configs: {e}")

    # Reconcile orphans and clear any stale pending counters at startup
    for image_tag in SUPPORTED_IMAGES:
        try:
            redis_client.delete(_get_pending_key(image_tag))
            reconcile_orphans(image_tag)
        except Exception as e:
            logger.error(f"[Daemon][{image_tag}] Orphan reconciliation error: {e}")

    executor = ThreadPoolExecutor(max_workers=PROVISION_CONCURRENCY, thread_name_prefix="pool-prov")
    reconcile_counter = 0

    try:
        while running:
            refresh_daemon_lock()

            # 1. Fetch active pool definitions from Redis dynamically
            try:
                active_pools = list_pool_defs()
            except Exception as e:
                logger.error(f"[Daemon] Error reading pool configurations from Redis: {e}")
                active_pools = []

            seen_tags = set()

            for pool in active_pools:
                if not pool or not running:
                    continue

                pool_name = pool.get("name", "unknown")
                image_tag = pool.get("image_tag")
                if not image_tag:
                    logger.warning(f"[Daemon] Skipping corrupt pool record '{pool_name}': missing image_tag")
                    continue

                seen_tags.add(image_tag)

                status = pool.get("status", "active")
                if status not in ("active", "suspended"):
                    logger.warning(f"[Daemon][{image_tag}] Unrecognized status '{status}', treating as active.")
                    status = "active"

                eff_target_buffer, eff_max_containers = get_effective_pool_config(pool)

                try:
                    if status == "suspended":
                        # Suspended pool: drain all idle containers (effective max = 0)
                        idle_count, busy_count = get_pool_metrics(image_tag)
                        scale_down_pool(image_tag, idle_count, busy_count, target_buffer=0, max_containers=0)
                    else:
                        # 1. Step 1: Read current pool metrics before scale-down
                        idle_count, busy_count = get_pool_metrics(image_tag)

                        # 2. Step 2: Apply scale-down rules with pool-specific target & max
                        scale_down_pool(image_tag, idle_count, busy_count, eff_target_buffer, eff_max_containers)

                        # 3. Step 3: Refresh metrics after scale-down to eliminate stale counts
                        # 4. Step 4-6: Scale up atomically reserving capacity and submitting async tasks
                        scale_up_pool(image_tag, eff_target_buffer, eff_max_containers)

                except Exception as e:
                    logger.error(f"[Daemon][{image_tag}] Error during pool reconciliation cycle: {e}")

            # Reconcile orphans periodically (every ~30 ticks / 60s)
            reconcile_counter += 1
            if reconcile_counter >= 30:
                reconcile_counter = 0
                for tag in seen_tags:
                    try:
                        reconcile_orphans(tag)
                    except Exception as e:
                        logger.error(f"[Daemon][{tag}] Periodic reconciliation error: {e}")

            # Responsive sleep in small increments to allow rapid shutdown response
            slept = 0.0
            while slept < CHECK_INTERVAL and running:
                time.sleep(0.1)
                slept += 0.1

    except Exception as e:
        logger.error(f"[Daemon] Unhandled loop exception: {e}", exc_info=True)
    finally:
        execute_shutdown_cleanup()


if __name__ == "__main__":
    run_daemon()
