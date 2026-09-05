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


def provision_new_container(image_tag):
    # Sanitize the image_tag to use as a prefix in the container name
    sanitized_tag = image_tag.replace(":", "_").replace("/", "_")
    container_id = f"{sanitized_tag}_{uuid.uuid4().hex[:8]}"

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

    redis_client.sadd(_get_idle_key(image_tag), container_id)
    print(f"[Pool] Provisioned {image_tag} warm container: {container_id}")
    return container_id


def acquire_container(image_tag, timeout_seconds=10):
    idle_key = _get_idle_key(image_tag)
    busy_key = _get_busy_key(image_tag)
    start_time = time.time()

    while time.time() - start_time < timeout_seconds:

        container_id = redis_client.srandmember(idle_key)

        if container_id:
            success = redis_client.smove(idle_key, busy_key, container_id)
            if success:
                return container_id

        time.sleep(random.uniform(0.05, 0.2))
    raise TimeoutError(f"Timeout: No warm {image_tag} containers became available in time.")


def release_container(image_tag, container_id):
    redis_client.smove(_get_busy_key(image_tag), _get_idle_key(image_tag), container_id)
    print(f"[Pool] Container {container_id} returned to {image_tag} idle pool.")
