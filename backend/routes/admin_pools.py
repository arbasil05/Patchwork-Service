import docker
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from redis import Redis

from dependencies.auth import get_current_admin
from config.settings import settings
from schema.poolSchema import PoolCreate, PoolPatch, PoolOut, AvailableImage
from storage.pool_defs import (
    seed_default_pools,
    list_pool_defs,
    get_pool_def,
    save_pool_def,
    update_pool_def,
    delete_pool_def,
)
from storage.image_defs import list_image_defs
from tasks.pool.pool_manager import _get_idle_key, _get_busy_key

redis_client = Redis(host=settings.REDIS_HOST, port=6379, decode_responses=True)
docker_client = docker.from_env()

router = APIRouter(prefix="/admin/pools", tags=["admin_pools"])

def _enrich_pool(pool: dict) -> PoolOut:
    image_tag = pool.get("image_tag", "")
    free = redis_client.scard(_get_idle_key(image_tag))
    occupied = redis_client.scard(_get_busy_key(image_tag))
    total = free + occupied

    return PoolOut(
        name=pool["name"],
        image_tag=image_tag,
        max_containers=pool.get("max_containers", 10),
        total=total,
        free=free,
        occupied=occupied,
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
        docker_client.images.get(payload.image_tag)
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
            container = docker_client.containers.get(container_id)
            container.kill()
        except Exception:
            pass

    # Flush Redis keys
    redis_client.delete(idle_key, busy_key)

    # Delete definition
    delete_pool_def(name)
    return None
