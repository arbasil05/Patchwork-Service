import json
import os
import re
import tempfile
import logging
from datetime import datetime, timezone

STORAGE_DIR = "/home/basil/Patchwork_Service/saved_docker_images"

def get_storage_dir():
    if not os.path.exists(STORAGE_DIR):
        os.makedirs(STORAGE_DIR, exist_ok=True)
    return STORAGE_DIR

def sanitize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    # Allow alphanumeric, hyphens, and underscores only
    return re.sub(r'[^a-zA-Z0-9_-]', '', name)

def get_file_path(name: str) -> str:
    sanitized = sanitize_name(name)
    if not sanitized or sanitized != name:
        raise ValueError(f"Invalid name '{name}'. Only alphanumeric characters, hyphens, and underscores are allowed.")
    return os.path.join(get_storage_dir(), f"{sanitized}.json")

def validate_image_def(data: dict):
    if not isinstance(data, dict):
        raise ValueError("Data must be a dictionary")
        
    name = data.get("name")
    if not name or not sanitize_name(name) or name != sanitize_name(name):
        raise ValueError("Name is required and must contain only alphanumeric, hyphens, and underscores")
        
    runtime = data.get("runtime")
    if runtime not in ["python", "node"]:
        raise ValueError("Runtime must be either 'python' or 'node'")

def save_image_def(data: dict) -> dict:
    validate_image_def(data)
    
    name = data["name"]
    file_path = get_file_path(name)
    
    now = datetime.now(timezone.utc).isoformat()
    
    if not os.path.exists(file_path):
        data["created_at"] = now
    elif "created_at" not in data:
        existing_data = get_image_def(name)
        if existing_data and "created_at" in existing_data:
             data["created_at"] = existing_data["created_at"]
        else:
             data["created_at"] = now
             
    data["updated_at"] = now
    
    # Ensure default fields
    if "dependencies" not in data:
        data["dependencies"] = []
    if "image_tag" not in data:
        data["image_tag"] = None
    if "status" not in data:
        data["status"] = "draft"
    if "build_log" not in data:
        data["build_log"] = None

    _atomic_write(file_path, data)
    return data

def get_image_def(name: str) -> dict | None:
    try:
        file_path = get_file_path(name)
    except ValueError:
        return None
        
    if not os.path.exists(file_path):
        return None
        
    return _read_json_file(file_path)

def list_image_defs() -> list[dict]:
    storage_dir = get_storage_dir()
    results = []
    
    for filename in os.listdir(storage_dir):
        if filename.endswith(".json"):
            file_path = os.path.join(storage_dir, filename)
            data = _read_json_file(file_path)
            if data is not None:
                results.append(data)
                
    return results

def update_image_def(name: str, updates: dict) -> dict:
    existing_data = get_image_def(name)
    if not existing_data:
        raise ValueError(f"Image definition '{name}' does not exist")
        
    # Name cannot be updated (or if it is, it must match)
    if "name" in updates and updates["name"] != name:
        raise ValueError("Cannot update the name of an existing image definition")
        
    # Merge updates
    merged_data = {**existing_data, **updates}
    
    # Validate the merged data
    validate_image_def(merged_data)
    
    now = datetime.now(timezone.utc).isoformat()
    merged_data["updated_at"] = now
    
    file_path = get_file_path(name)
    _atomic_write(file_path, merged_data)
    
    return merged_data

def delete_image_def(name: str) -> bool:
    try:
        file_path = get_file_path(name)
    except ValueError:
        return False
        
    if os.path.exists(file_path):
        os.remove(file_path)
        return True
    return False

def _atomic_write(file_path: str, data: dict):
    directory = os.path.dirname(file_path)
    # Create temp file in the same directory to ensure atomic rename
    fd, temp_path = tempfile.mkstemp(dir=directory, prefix="tmp_img_def_")
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, file_path)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e

def _read_json_file(file_path: str) -> dict | None:
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to read image definition from {file_path}: {e}")
        return None
