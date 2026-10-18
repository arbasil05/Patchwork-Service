import uuid
import docker
from redis import Redis
import time
import random
from config.settings import settings

redis_client = Redis(host=settings.REDIS_HOST, port=6379, decode_responses=True)
docker_client = docker.from_env()


def _get_idle_key(image_tag):
    return f"pool:{image_tag}:idle"


def _get_busy_key(image_tag):
    return f"pool:{image_tag}:busy"


def _get_pending_key(image_tag):
    return f"pool:{image_tag}:pending"


def create_container(image_tag, register_in_pool=True):
    # Sanitize the image_tag to use as a prefix in the container name
    sanitized_tag = image_tag.replace(":", "_").replace("/", "_")
    prefix = f"{sanitized_tag}_" if register_in_pool else f"ondemand_{sanitized_tag}_"
    container_id = f"{prefix}{uuid.uuid4().hex[:8]}"

    run_kwargs = {
        "image": image_tag,
        # this command will keep the container running forever
        "command": ["sh", "-c", "tail -f /dev/null"],
        "name": container_id,
        "detach": True,
        "remove": True,
        "network_disabled": True,
        "mem_limit": "256m",
        "pids_limit": 256,
    }

    if "express" in image_tag or "node" in image_tag:
        # Express needs the pre-installed node_modules inside /workspace to be visible.
        # We cannot mount an empty tmpfs over it. To allow writing submission files,
        # we must disable read_only for this image.
        run_kwargs["read_only"] = False
        run_kwargs["tmpfs"] = {'/tmp': 'exec'}
    else:
        # Python frameworks don't need dependencies in /workspace, so they can use
        # a strict read-only root with an empty tmpfs for the workspace.
        run_kwargs["read_only"] = True
        run_kwargs["tmpfs"] = {'/tmp': 'exec', '/workspace': 'exec,size=64m,uid=1000,gid=1000'}

    container = docker_client.containers.run(**run_kwargs)

    if register_in_pool:
        redis_client.sadd(_get_idle_key(image_tag), container_id)
        print(f"[Pool] Provisioned {image_tag} warm container: {container_id}")
    return container_id


def provision_new_container(image_tag):
    return create_container(image_tag, register_in_pool=True)


class PoolSuspendedError(Exception):
    """Raised when a container cannot be acquired because the pool is suspended."""
    pass


def is_pool_suspended(image_tag: str) -> bool:
    try:
        pool_names = redis_client.smembers("pool_configs")
        for name in pool_names:
            pool_data = redis_client.hgetall(f"pool_config:{name}")
            if pool_data.get("image_tag") == image_tag:
                return pool_data.get("status") == "suspended"
    except Exception:
        pass
    return False


def acquire_container(image_tag, timeout_seconds=10, return_stats=False):
    idle_key = _get_idle_key(image_tag)
    busy_key = _get_busy_key(image_tag)
    start_time = time.time()

    # Fail fast if pool is currently suspended
    if is_pool_suspended(image_tag):
        raise PoolSuspendedError(f"Pool for image '{image_tag}' is suspended.")

    attempts = 0
    while time.time() - start_time < timeout_seconds:
        attempts += 1
        # Check on every loop iteration so waiting submissions fail fast if suspended mid-wait
        if is_pool_suspended(image_tag):
            raise PoolSuspendedError(f"Pool for image '{image_tag}' is suspended.")

        container_id = redis_client.srandmember(idle_key)

        if container_id:
            success = redis_client.smove(idle_key, busy_key, container_id)
            if success:
                acquire_dur = time.time() - start_time
                pool_hit = (attempts == 1)
                if return_stats:
                    return container_id, acquire_dur, pool_hit
                return container_id

        time.sleep(random.uniform(0.05, 0.2))
    raise TimeoutError(f"Timeout: No warm {image_tag} containers became available in time.")


def release_container(image_tag, container_id):
    redis_client.smove(_get_busy_key(image_tag), _get_idle_key(image_tag), container_id)
    print(f"[Pool] Container {container_id} returned to {image_tag} idle pool.")
