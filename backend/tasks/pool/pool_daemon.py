import sys
import os

_file_path = os.path.abspath(__file__.rstrip("\\/"))
_current_dir = os.path.dirname(_file_path)
_backend_root = os.path.join(_current_dir, "..", "..")

# Ensure the backend root is on the Python path to import config
sys.path.insert(0, _backend_root)
# Ensure the current directory is on the Python path to import pool_manager when run with trailing slashes
sys.path.insert(0, _current_dir)

import signal
import time
import docker
import redis
from pool_manager import provision_new_container, _get_idle_key, _get_busy_key
from config.settings import settings

sys.stdout.reconfigure(line_buffering=True)

redis_client = redis.Redis(host=settings.REDIS_HOST, port=6379, decode_responses=True)
docker_client = docker.from_env()

# Read supported images from centralized settings
SUPPORTED_IMAGES = settings.SUPPORTED_IMAGES.split(",")

TARGET_BUFFER = 3
MAX_POOL_SIZE = 10
CHECK_INTERVAL = 2

running = True


def graceful_shutdown(signum, frame):
    """Handle Ctrl+C: kill every pooled container and flush Redis pool keys."""
    global running
    running = False
    print("\n[Daemon] Shutting down — killing all pooled containers...")

    for image_tag in SUPPORTED_IMAGES:
        idle_key = _get_idle_key(image_tag)
        busy_key = _get_busy_key(image_tag)

        all_ids = redis_client.smembers(idle_key) | redis_client.smembers(busy_key)
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

        redis_client.delete(idle_key, busy_key)
        print(f"[Daemon][{image_tag}] Redis keys flushed.")

    print("[Daemon] Shutdown complete.")
    sys.exit(0)


signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)


def get_pool_metrics(image_tag):
    idle_count = redis_client.scard(_get_idle_key(image_tag))
    busy_count = redis_client.scard(_get_busy_key(image_tag))
    return idle_count, busy_count

def scale_up_pool(image_tag, idle_count, total_containers):
    if idle_count < TARGET_BUFFER:
        needed = TARGET_BUFFER - idle_count
        available_slots = MAX_POOL_SIZE - total_containers
        to_spawn = min(needed, available_slots)

        if to_spawn > 0:
            print(f"[Daemon][{image_tag}] Pool low! Idle: {idle_count}/{TARGET_BUFFER}. Scaling up by {to_spawn}...")
            for _ in range(to_spawn):
                try:
                    provision_new_container(image_tag)
                except Exception as e:
                    print(f"[Daemon][{image_tag}] Failed to provision container: {e}")
                    break

def scale_down_pool(image_tag, idle_count, busy_count):
    target = TARGET_BUFFER if busy_count == 0 else TARGET_BUFFER + 5

    if idle_count > target:
        excess = idle_count - target
        print(f"[Daemon][{image_tag}] {'Idle-only trim' if busy_count == 0 else 'Pool saturated'}. Idle: {idle_count} → {target}. Reaping {excess} containers...")

        idle_key = _get_idle_key(image_tag)
        for _ in range(excess):
            container_id = redis_client.spop(idle_key)
            if not container_id:
                break
            try:
                container = docker_client.containers.get(container_id)
                container.kill()
                print(f"[Daemon][{image_tag}] Reaped: {container_id}")
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

    orphans = [c for c in all_runner_containers if c.name not in known_ids]
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
    print(f"[Daemon] Preemptive scaling daemon started. Target: {TARGET_BUFFER}, Max: {MAX_POOL_SIZE} (per image)")
    print(f"[Daemon] Supported images: {', '.join(SUPPORTED_IMAGES)}")
    print(f"[Daemon] Press Ctrl+C to shut down and kill all containers.\n")

    for image_tag in SUPPORTED_IMAGES:
        reconcile_orphans(image_tag)

    while running:
        try:
            for image_tag in SUPPORTED_IMAGES:
                idle_count, busy_count = get_pool_metrics(image_tag)
                total_containers = idle_count + busy_count
                scale_up_pool(image_tag, idle_count, total_containers)
                scale_down_pool(image_tag, idle_count, busy_count)

        except Exception as e:
            print(f"[Daemon] Loop error: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run_daemon()
