import docker
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from redis import Redis

from dependencies.auth import get_current_admin
from config.settings import settings
from schema.poolSchema import PoolCreate, PoolPatch, PoolOut, AvailableImage, ContainerOut, PoolDetailOut
from storage.pool_defs import (
    seed_default_pools,
    list_pool_defs,
    get_pool_def,
    save_pool_def,
    update_pool_def,
    delete_pool_def,
)
from storage.image_defs import list_image_defs

redis_client = Redis(host=settings.REDIS_HOST, port=6379, decode_responses=True)
_docker_client = None

def get_docker_client():
    global _docker_client
    if _docker_client is None:
        _docker_client = docker.from_env()
    return _docker_client

def _get_idle_key(image_tag: str) -> str:
    return f"pool:{image_tag}:idle"

def _get_busy_key(image_tag: str) -> str:
    return f"pool:{image_tag}:busy"

router = APIRouter(prefix="/admin/pools", tags=["admin_pools"])

def _enrich_pool(pool: dict) -> PoolOut:
    image_tag = pool.get("image_tag", "")
    free = redis_client.scard(_get_idle_key(image_tag))
    occupied = redis_client.scard(_get_busy_key(image_tag))
    total = free + occupied

    status_val = pool.get("status", "active")
    if status_val not in ("active", "suspended"):
        status_val = "active"

    return PoolOut(
        name=pool["name"],
        image_tag=image_tag,
        max_containers=pool.get("max_containers", 10),
        total=total,
        free=free,
        occupied=occupied,
        status=status_val,
        is_default=pool.get("is_default", False),
        created_at=pool.get("created_at", ""),
        updated_at=pool.get("updated_at", ""),
    )

@router.get("", response_model=List[PoolOut])
def get_all_pools(admin: str = Depends(get_current_admin)):
    seed_default_pools()
    pools = list_pool_defs()
    return [_enrich_pool(p) for p in pools]

@router.get("/available-images", response_model=List[AvailableImage])
def get_available_images(admin: str = Depends(get_current_admin)):
    seed_default_pools()
    existing_pools = list_pool_defs()
    pooled_tags = {p["image_tag"] for p in existing_pools}

    results = []

    # 1. Default system images from settings.SUPPORTED_IMAGES
    default_tags = [t.strip() for t in settings.SUPPORTED_IMAGES.split(",") if t.strip()]
    for tag in default_tags:
        clean_name = tag.split(":")[0].replace("/", "-")
        results.append(
            AvailableImage(
                image_tag=tag,
                display_name=f"{clean_name} (system default)",
                runtime="python" if "django" in tag else "node",
                is_default=True,
                already_pooled=tag in pooled_tags,
            )
        )

    # 2. Successfully built admin images
    saved_images = list_image_defs()
    for img in saved_images:
        tag = img.get("image_tag")
        if img.get("status") == "success" and tag:
            # Avoid duplicate entries if already added as default
            if any(r.image_tag == tag for r in results):
                continue
            results.append(
                AvailableImage(
                    image_tag=tag,
                    display_name=f"{img.get('name')} ({tag})",
                    runtime=img.get("runtime"),
                    is_default=False,
                    already_pooled=tag in pooled_tags,
                )
            )

    return results

@router.post("", response_model=PoolOut, status_code=status.HTTP_201_CREATED)
def create_pool(payload: PoolCreate, admin: str = Depends(get_current_admin)):
    seed_default_pools()

    # 1. Check if pool name already exists
    if get_pool_def(payload.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Pool '{payload.name}' already exists."
        )

    # 2. Check if a pool with this image_tag already exists
    existing = list_pool_defs()
    if any(p.get("image_tag") == payload.image_tag for p in existing):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A pool for image tag '{payload.image_tag}' already exists."
        )

    # 3. Validate image tag exists in Docker daemon
    try:
        get_docker_client().images.get(payload.image_tag)
    except docker.errors.ImageNotFound:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Image tag '{payload.image_tag}' does not exist in Docker daemon."
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error inspecting Docker for image tag '{payload.image_tag}': {str(e)}"
        )

    # 4. Save pool definition
    try:
        created = save_pool_def(
            name=payload.name,
            image_tag=payload.image_tag,
            max_containers=payload.max_containers,
            is_default=False
        )
        return _enrich_pool(created)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("/{name}", response_model=PoolOut)
def get_pool(name: str, admin: str = Depends(get_current_admin)):
    seed_default_pools()
    pool = get_pool_def(name)
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool '{name}' not found."
        )
    return _enrich_pool(pool)

@router.patch("/{name}", response_model=PoolOut)
def scale_pool(name: str, payload: PoolPatch, admin: str = Depends(get_current_admin)):
    seed_default_pools()
    pool = get_pool_def(name)
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool '{name}' not found."
        )

    updated = update_pool_def(name, {"max_containers": payload.max_containers})
    return _enrich_pool(updated)

@router.post("/{name}/suspend", response_model=PoolOut)
def suspend_pool(name: str, admin: str = Depends(get_current_admin)):
    seed_default_pools()
    pool = get_pool_def(name)
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool '{name}' not found."
        )

    if pool.get("status") == "suspended":
        return _enrich_pool(pool)

    updated = update_pool_def(name, {"status": "suspended"})
    return _enrich_pool(updated)

@router.post("/{name}/activate", response_model=PoolOut)
def activate_pool(name: str, admin: str = Depends(get_current_admin)):
    seed_default_pools()
    pool = get_pool_def(name)
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool '{name}' not found."
        )

    if pool.get("status") == "active":
        return _enrich_pool(pool)

    updated = update_pool_def(name, {"status": "active"})
    return _enrich_pool(updated)

@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pool(name: str, admin: str = Depends(get_current_admin)):
    seed_default_pools()
    pool = get_pool_def(name)
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool '{name}' not found."
        )

    if pool.get("is_default"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete default system pool '{name}'."
        )

    image_tag = pool.get("image_tag")
    busy_key = _get_busy_key(image_tag)
    idle_key = _get_idle_key(image_tag)

    occupied = redis_client.scard(busy_key)
    if occupied > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot delete pool '{name}' while {occupied} container(s) are occupied."
        )

    # Clean up idle containers from Docker
    idle_ids = redis_client.smembers(idle_key)
    for container_id in idle_ids:
        try:
            container = get_docker_client().containers.get(container_id)
            container.kill()
        except Exception:
            pass

    # Flush Redis keys
    redis_client.delete(idle_key, busy_key)

    # Delete definition
    delete_pool_def(name)
    return None

def _inspect_container_info(cid: str, state: str) -> ContainerOut:
    try:
        c = get_docker_client().containers.get(cid)
        short_id = c.short_id
        name = c.name
        started_at = c.attrs.get("State", {}).get("StartedAt")
        status_str = c.status

        memory_used_bytes = None
        memory_limit_bytes = None

        if status_str == "running":
            try:
                stats = c.stats(stream=False)
                mem_stats = stats.get("memory_stats", {})
                usage = mem_stats.get("usage", 0)
                stats_dict = mem_stats.get("stats", {})
                inactive_file = stats_dict.get("inactive_file", stats_dict.get("total_inactive_file", 0))
                memory_used_bytes = max(0, usage - inactive_file)
                memory_limit_bytes = mem_stats.get("limit")
            except Exception:
                pass

        return ContainerOut(
            id=short_id,
            name=name,
            state=state,
            memory_used_bytes=memory_used_bytes,
            memory_limit_bytes=memory_limit_bytes,
            started_at=started_at,
        )
    except docker.errors.NotFound:
        return ContainerOut(
            id=cid[:12] if len(cid) >= 12 else cid,
            name=cid,
            state="missing",
            memory_used_bytes=None,
            memory_limit_bytes=None,
            started_at=None,
        )
    except Exception:
        return ContainerOut(
            id=cid[:12] if len(cid) >= 12 else cid,
            name=cid,
            state=state,
            memory_used_bytes=None,
            memory_limit_bytes=None,
            started_at=None,
        )

@router.get("/{name}/containers", response_model=PoolDetailOut)
def get_pool_containers(name: str, admin: str = Depends(get_current_admin)):
    seed_default_pools()
    pool = get_pool_def(name)
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool '{name}' not found."
        )

    image_tag = pool.get("image_tag")
    idle_key = _get_idle_key(image_tag)
    busy_key = _get_busy_key(image_tag)

    idle_ids = list(redis_client.smembers(idle_key))
    busy_ids = list(redis_client.smembers(busy_key))

    tasks = [(cid, "idle") for cid in idle_ids] + [(cid, "busy") for cid in busy_ids]
    containers_out = []

    if tasks:
        with ThreadPoolExecutor(max_workers=min(10, len(tasks))) as executor:
            future_to_cid = {executor.submit(_inspect_container_info, cid, state): cid for cid, state in tasks}
            for future in as_completed(future_to_cid, timeout=3.5):
                try:
                    res = future.result()
                    containers_out.append(res)
                except Exception:
                    cid = future_to_cid[future]
                    containers_out.append(ContainerOut(
                        id=cid[:12] if len(cid) >= 12 else cid,
                        name=cid,
                        state="idle" if cid in idle_ids else "busy",
                    ))

    # Sort containers: busy first, then idle, then missing; then by name
    state_order = {"busy": 0, "idle": 1, "missing": 2}
    containers_out.sort(key=lambda c: (state_order.get(c.state, 9), c.name))

    enriched_pool = _enrich_pool(pool)
    return PoolDetailOut(pool=enriched_pool, containers=containers_out)

@router.delete("/{name}/containers/{container_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pool_container(
    name: str,
    container_id: str,
    force: bool = False,
    admin: str = Depends(get_current_admin)
):
    seed_default_pools()
    pool = get_pool_def(name)
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pool '{name}' not found."
        )

    image_tag = pool.get("image_tag")
    idle_key = _get_idle_key(image_tag)
    busy_key = _get_busy_key(image_tag)

    idle_members = redis_client.smembers(idle_key)
    busy_members = redis_client.smembers(busy_key)
    all_members = idle_members | busy_members

    # Resolve container_id to the exact Redis member name
    target_name = None
    if container_id in all_members:
        target_name = container_id
    else:
        for member in all_members:
            if member.startswith(container_id) or container_id in member:
                target_name = member
                break
        if not target_name:
            try:
                c = get_docker_client().containers.get(container_id)
                if c.name in all_members:
                    target_name = c.name
            except Exception:
                pass

    if not target_name:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Container '{container_id}' not found in pool '{name}'."
        )

    # Check whether the container exists in Docker
    container_exists_in_docker = True
    try:
        get_docker_client().containers.get(target_name)
    except docker.errors.NotFound:
        container_exists_in_docker = False

    # Handle Missing container (in Redis, but not in Docker)
    if not container_exists_in_docker:
        redis_client.srem(idle_key, target_name)
        redis_client.srem(busy_key, target_name)
        return None

    # Handle Idle container
    if target_name in idle_members:
        claimed = redis_client.srem(idle_key, target_name)
        if claimed == 0:
            if redis_client.sismember(busy_key, target_name):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Container '{container_id}' was just checked out and is now busy."
                )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Container '{container_id}' not found in pool."
            )

        try:
            c = get_docker_client().containers.get(target_name)
            c.kill()
        except docker.errors.NotFound:
            pass
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to kill container in Docker: {str(e)}"
            )
        return None

    # Handle Busy container
    if target_name in busy_members:
        if not force:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Container '{container_id}' is currently busy running a job. Use force=true to kill it."
            )

        redis_client.srem(busy_key, target_name)
        try:
            c = get_docker_client().containers.get(target_name)
            c.kill()
        except docker.errors.NotFound:
            pass
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to force-kill container in Docker: {str(e)}"
            )
        return None

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Container '{container_id}' not found in pool."
    )

