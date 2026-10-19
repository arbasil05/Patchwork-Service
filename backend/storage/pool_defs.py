import re
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from redis import Redis
from config.settings import settings

redis_client = Redis(host=settings.REDIS_HOST, port=6379, decode_responses=True)

POOLS_SET_KEY = "pool_configs"
POOL_HASH_PREFIX = "pool_config:"

def sanitize_pool_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    return re.sub(r'[^a-zA-Z0-9_-]', '', name)

def seed_default_pools(force_sync_defaults: bool = False):
    """
    Ensure default system pools (from settings.SUPPORTED_IMAGES) exist in Redis.
    Default targets: Django=8, Express=3. Default max_containers: 15.
    If force_sync_defaults is True, updates existing default pools to new defaults if they have old default values.
    """
    default_tags = [t.strip() for t in settings.SUPPORTED_IMAGES.split(",") if t.strip()]
    now = datetime.now(timezone.utc).isoformat()

    default_configs = {
        "patchwork-django": {"max_containers": 20, "target_buffer": 12},
        "patchwork-express": {"max_containers": 20, "target_buffer": 12},
    }

    for image_tag in default_tags:
        # Generate stable name from tag (e.g. patchwork-django:latest -> patchwork-django)
        clean_tag = image_tag.split(":")[0].replace("/", "-")
        pool_name = clean_tag

        cfg = default_configs.get(pool_name, {"max_containers": 15, "target_buffer": 3})

        key = f"{POOL_HASH_PREFIX}{pool_name}"
        if not redis_client.exists(key):
            # Check if another pool already uses this image_tag
            existing = list_pool_defs()
            if any(p.get("image_tag") == image_tag for p in existing):
                continue

            redis_client.hset(key, mapping={
                "name": pool_name,
                "image_tag": image_tag,
                "max_containers": str(cfg["max_containers"]),
                "target_buffer": str(cfg["target_buffer"]),
                "is_default": "1",
                "status": "active",
                "created_at": now,
                "updated_at": now,
            })
            redis_client.sadd(POOLS_SET_KEY, pool_name)
        else:
            # Check if existing default pool needs to be updated or missing fields backfilled
            existing_data = redis_client.hgetall(key)
            updates = {}
            if force_sync_defaults:
                # Update max_containers if missing
                if existing_data.get("max_containers") in (None, ""):
                    updates["max_containers"] = str(cfg["max_containers"])
                # Update target_buffer if missing
                if existing_data.get("target_buffer") in (None, ""):
                    updates["target_buffer"] = str(cfg["target_buffer"])
            else:
                # Always ensure target_buffer is populated for default pools if missing
                if "target_buffer" not in existing_data or existing_data["target_buffer"] == "":
                    updates["target_buffer"] = str(cfg["target_buffer"])

            if updates:
                updates["updated_at"] = now
                redis_client.hset(key, mapping=updates)

def get_pool_def(name: str) -> Optional[Dict[str, Any]]:
    key = f"{POOL_HASH_PREFIX}{name}"
    data = redis_client.hgetall(key)
    if not data:
        for pool_name in redis_client.smembers(POOLS_SET_KEY):
            pdata = redis_client.hgetall(f"{POOL_HASH_PREFIX}{pool_name}")
            if pdata.get("image_tag") == name or pool_name == name.split(":")[0].replace("/", "-"):
                return _format_pool_data(pdata)
        return None
    
    return _format_pool_data(data)

def list_pool_defs() -> List[Dict[str, Any]]:
    pool_names = redis_client.smembers(POOLS_SET_KEY)
    results = []
    
    for name in sorted(pool_names):
        data = get_pool_def(name)
        if data:
            results.append(data)
    return results

def save_pool_def(name: str, image_tag: str, max_containers: int = 15, target_buffer: Optional[int] = None, is_default: bool = False) -> Dict[str, Any]:
    sanitized = sanitize_pool_name(name)
    if not sanitized or sanitized != name:
        raise ValueError("Pool name must contain only alphanumeric characters, hyphens, and underscores.")

    now = datetime.now(timezone.utc).isoformat()
    key = f"{POOL_HASH_PREFIX}{name}"

    mapping = {
        "name": name,
        "image_tag": image_tag,
        "max_containers": str(max_containers),
        "is_default": "1" if is_default else "0",
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    if target_buffer is not None:
        mapping["target_buffer"] = str(target_buffer)

    redis_client.hset(key, mapping=mapping)
    redis_client.sadd(POOLS_SET_KEY, name)

    return get_pool_def(name)

def update_pool_def(name: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    key = f"{POOL_HASH_PREFIX}{name}"
    if not redis_client.exists(key):
        # Fallback: check if name matches an image_tag or sanitized name
        for pool_name in redis_client.smembers(POOLS_SET_KEY):
            pdata = redis_client.hgetall(f"{POOL_HASH_PREFIX}{pool_name}")
            if pdata.get("image_tag") == name or pool_name == name.split(":")[0].replace("/", "-"):
                name = pool_name
                key = f"{POOL_HASH_PREFIX}{name}"
                break
        else:
            raise ValueError(f"Pool '{name}' does not exist.")

    now = datetime.now(timezone.utc).isoformat()
    mapping = {"updated_at": now}

    if "max_containers" in updates and updates["max_containers"] is not None:
        mapping["max_containers"] = str(updates["max_containers"])
    if "target_buffer" in updates and updates["target_buffer"] is not None:
        mapping["target_buffer"] = str(updates["target_buffer"])
    if "status" in updates and updates["status"] is not None:
        mapping["status"] = str(updates["status"])

    redis_client.hset(key, mapping=mapping)
    return get_pool_def(name)

def delete_pool_def(name: str) -> bool:
    key = f"{POOL_HASH_PREFIX}{name}"
    if not redis_client.exists(key):
        return False
    
    redis_client.delete(key)
    redis_client.srem(POOLS_SET_KEY, name)
    return True

def _format_pool_data(data: Dict[str, str]) -> Dict[str, Any]:
    try:
        raw_status = data.get("status")
        status = "active" if raw_status is None else raw_status
        raw_max = data.get("max_containers")
        max_c = int(raw_max) if raw_max is not None and raw_max != "" else 15

        raw_tb = data.get("target_buffer")
        target_buffer = int(raw_tb) if raw_tb is not None and raw_tb != "" else None

        return {
            "name": data.get("name", ""),
            "image_tag": data.get("image_tag", ""),
            "max_containers": max_c,
            "target_buffer": target_buffer,
            "is_default": data.get("is_default", "0") in ("1", "true", "True"),
            "status": status,
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
        }
    except Exception:
        return None
