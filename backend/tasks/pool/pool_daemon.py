import sys
import os

_file_path = os.path.abspath(__file__.rstrip("\\/"))
_current_dir = os.path.dirname(_file_path)
_backend_root = os.path.join(_current_dir, "..", "..")

# Ensure the backend root is on the Python path to import config and storage
sys.path.insert(0, _backend_root)
# Ensure the current directory is on the Python path to import pool_manager when run with trailing slashes
sys.path.insert(0, _current_dir)

import signal
import time
import docker
import redis
from pool_manager import provision_new_container, _get_idle_key, _get_busy_key
from config.settings import settings
from storage.pool_defs import seed_default_pools, list_pool_defs

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

redis_client = redis.Redis(host=settings.REDIS_HOST, port=6379, decode_responses=True)
docker_client = docker.from_env()

# Read supported images from centralized settings
SUPPORTED_IMAGES = [t.strip() for t in settings.SUPPORTED_IMAGES.split(",") if t.strip()]

TARGET_BUFFER = int(os.getenv("TARGET_BUFFER", 3))
MAX_POOL_SIZE = int(os.getenv("MAX_POOL_SIZE", 10))
CHECK_INTERVAL = float(os.getenv("CHECK_INTERVAL", 2))

running = True


def graceful_shutdown(signum, frame):
    """Handle Ctrl+C: kill every pooled container and flush Redis pool keys."""
    global running
    running = False
    print("\n[Daemon] Shutting down — killing all pooled containers...")

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

        try:
            all_ids = redis_client.smembers(idle_key) | redis_client.smembers(busy_key)
        except Exception:
            all_ids = set()

        if not all_ids:
            print(f"[Daemon][{image_tag}] No containers to clean up.")
            continue

        print(f"[Daemon][{image_tag}] Killing {len(all_ids)} container(s)...")
        for container_id in all_ids:
            try:
                container = docker_client.containers.get(container_id)
                container.kill()
                print(f"  ✓ Killed {container_id}")
            except docker.errors.NotFound:
                print(f"  ✗ {container_id} already gone")
            except Exception as e:
                print(f"  ✗ Error killing {container_id}: {e}")

        try:
            redis_client.delete(idle_key, busy_key)
            print(f"[Daemon][{image_tag}] Redis keys flushed.")
        except Exception as e:
            print(f"[Daemon][{image_tag}] Error flushing Redis keys: {e}")

    print("[Daemon] Shutdown complete.")
    sys.exit(0)


signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)


def get_pool_metrics(image_tag):
    idle_count = redis_client.scard(_get_idle_key(image_tag))
    busy_count = redis_client.scard(_get_busy_key(image_tag))
    return idle_count, busy_count


def scale_up_pool(image_tag, idle_count, total_containers, max_containers):
    target_idle = min(TARGET_BUFFER, max_containers)
    if target_idle <= 0 or idle_count >= target_idle:
        return

    needed = target_idle - idle_count
    available_slots = max(0, max_containers - total_containers)
    to_spawn = min(needed, available_slots)

    if to_spawn > 0:
        print(f"[Daemon][{image_tag}] Pool low! Idle: {idle_count}/{target_idle} (Max: {max_containers}). Scaling up by {to_spawn}...")
        for _ in range(to_spawn):
            try:
                t_prov_start = time.time()
                provision_new_container(image_tag)
                t_prov_dur = time.time() - t_prov_start
                try:
                    csv_dir = os.path.abspath(os.path.join(_backend_root, "..", "eval", "results"))
                    os.makedirs(csv_dir, exist_ok=True)
                    csv_path = os.path.join(csv_dir, "daemon_provision_times.csv")
                    header_needed = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
                    with open(csv_path, "a", encoding="utf-8") as f:
                        if header_needed:
                            f.write("timestamp,image_tag,seconds\n")
                        f.write(f"{t_prov_start},{image_tag},{t_prov_dur:.6f}\n")
                except Exception:
                    pass
            except Exception as e:
                print(f"[Daemon][{image_tag}] Failed to provision container: {e}")
                break


def scale_down_pool(image_tag, idle_count, busy_count, max_containers):
    total_containers = idle_count + busy_count
    idle_key = _get_idle_key(image_tag)

    # 1. Ceiling scale-down rule: If total exceeds max_containers, reap idle containers (never busy)
    if total_containers > max_containers and idle_count > 0:
        excess_total = total_containers - max_containers
        to_reap = min(excess_total, idle_count)
        print(f"[Daemon][{image_tag}] Total ({total_containers}) exceeds ceiling ({max_containers}). Draining {to_reap} idle container(s)...")

        for _ in range(to_reap):
            container_id = redis_client.spop(idle_key)
            if not container_id:
                break
            try:
                container = docker_client.containers.get(container_id)
                container.kill()
                print(f"[Daemon][{image_tag}] Reaped (ceiling): {container_id}")
            except docker.errors.NotFound:
                pass
            except Exception as e:
                print(f"[Daemon][{image_tag}] Error tearing down {container_id}: {e}")

            idle_count -= 1
            total_containers -= 1

    # 2. Buffer scale-down rule: Idle-only trim when idle exceeds buffer target
    target_buffer = min(TARGET_BUFFER, max_containers)
    target = target_buffer if busy_count == 0 else target_buffer + 5

    if idle_count > target:
        excess = idle_count - target
        print(f"[Daemon][{image_tag}] {'Idle-only trim' if busy_count == 0 else 'Pool saturated'}. Idle: {idle_count} -> {target}. Reaping {excess} containers...")

        for _ in range(excess):
            container_id = redis_client.spop(idle_key)
            if not container_id:
                break
            try:
                container = docker_client.containers.get(container_id)
                container.kill()
                print(f"[Daemon][{image_tag}] Reaped (buffer): {container_id}")
            except docker.errors.NotFound:
                pass
            except Exception as e:
                print(f"[Daemon][{image_tag}] Error tearing down {container_id}: {e}")


def reconcile_orphans(image_tag):
    idle_key = _get_idle_key(image_tag)
    busy_key = _get_busy_key(image_tag)
    known_ids = redis_client.smembers(idle_key) | redis_client.smembers(busy_key)

    sanitized_tag = image_tag.replace(":", "_").replace("/", "_")
    all_runner_containers = docker_client.containers.list(filters={"name": f"{sanitized_tag}_"})
    running_names = {c.name for c in all_runner_containers}

    orphans = [c for c in all_runner_containers if c.name not in known_ids and not c.name.startswith("ondemand_")]
    if orphans:
        print(f"[Daemon][{image_tag}] Found {len(orphans)} orphaned container(s). Reaping...")
        for c in orphans:
            try:
                c.kill()
                print(f"  ✓ Reaped orphan: {c.name}")
            except Exception as e:
                print(f"  ✗ Failed to reap {c.name}: {e}")

    stale_ids = known_ids - running_names
    if stale_ids:
        print(f"[Daemon][{image_tag}] Found {len(stale_ids)} stale Redis entries. Cleaning...")
        for stale_id in stale_ids:
            redis_client.srem(idle_key, stale_id)
            redis_client.srem(busy_key, stale_id)
            print(f"  ✓ Removed stale: {stale_id}")

    if not orphans and not stale_ids:
        print(f"[Daemon][{image_tag}] Pool is clean.")


def run_daemon():
    print(f"[Daemon] Dynamic pool management daemon started.")
    print(f"[Daemon] Default target buffer: {TARGET_BUFFER}, Hard cap: {MAX_POOL_SIZE}")
    print(f"[Daemon] Supported default images: {', '.join(SUPPORTED_IMAGES)}")
    print(f"[Daemon] Press Ctrl+C to shut down and kill all containers.\n")

    # Initial seeding of default pools
    try:
        seed_default_pools()
    except Exception as e:
        print(f"[Daemon] Warning: Failed to seed default pools on startup: {e}")

    # Reconcile orphans for default images at startup
    for image_tag in SUPPORTED_IMAGES:
        try:
            reconcile_orphans(image_tag)
        except Exception as e:
            print(f"[Daemon][{image_tag}] Orphan reconciliation error: {e}")

    reconcile_counter = 0

    while running:
        try:
            # 1. Fetch active pool definitions from Redis
            try:
                active_pools = list_pool_defs()
            except Exception as e:
                print(f"[Daemon] Error reading pool configurations from Redis: {e}")
                active_pools = []

            # Always ensure default images are managed
            seen_tags = set()

            for pool in active_pools:
                if not pool:
                    continue

                pool_name = pool.get("name", "unknown")
                image_tag = pool.get("image_tag")

                try:
                    if not image_tag:
                        print(f"[Daemon] Skipping corrupt pool record '{pool_name}': missing image_tag")
                        continue

                    raw_max = pool.get("max_containers")
                    try:
                        max_containers = int(raw_max)
                    except (ValueError, TypeError):
                        print(f"[Daemon] Skipping corrupt pool record '{pool_name}': invalid max_containers '{raw_max}'")
                        continue

                    if max_containers < 0:
                        print(f"[Daemon] Skipping pool '{pool_name}': negative max_containers {max_containers}")
                        continue

                    status = pool.get("status", "active")
                    if status not in ("active", "suspended"):
                        print(f"[Daemon][{image_tag or pool_name}] Warning: unrecognized pool status '{status}'. Treating as active.")
                        status = "active"

                    seen_tags.add(image_tag)

                    idle_count, busy_count = get_pool_metrics(image_tag)
                    total_containers = idle_count + busy_count

                    if status == "suspended":
                        # Reuse existing scale-down logic with effective max of 0 to reap idle containers
                        scale_down_pool(image_tag, idle_count, busy_count, max_containers=0)
                        # Skip scale-up entirely for suspended pool
                    else:
                        scale_down_pool(image_tag, idle_count, busy_count, max_containers)
                        scale_up_pool(image_tag, idle_count, total_containers, max_containers)

                except Exception as e:
                    print(f"[Daemon][{image_tag or pool_name}] Error managing pool: {e}")

            # Reconcile orphans periodically (every ~30 ticks / 60s)
            reconcile_counter += 1
            if reconcile_counter >= 30:
                reconcile_counter = 0
                for tag in seen_tags:
                    try:
                        reconcile_orphans(tag)
                    except Exception as e:
                        print(f"[Daemon][{tag}] Periodic reconciliation error: {e}")

        except Exception as e:
            print(f"[Daemon] Loop error: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_daemon()
