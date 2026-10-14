import json
import re
import os
import shutil
import tempfile
import time
import docker
import hashlib

PYTHON_BASELINE_DOCKERFILE = """\
FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN useradd --create-home --shell /bin/bash runner

RUN apt-get update && \\
    apt-get install -y --no-install-recommends bash git && \\
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /workspace

USER runner

CMD ["tail", "-f", "/dev/null"]
"""

# Note: The baseline provided in the spec used `npm ci`.
# Since generating a fully accurate package-lock.json server-side without a real 
# node resolution environment is impractical, we have explicitly changed `npm ci` 
# to `npm install` in this Dockerfile template. This allows npm to resolve the tree 
# dynamically during the image build step based on the provided package.json.
NODE_BASELINE_DOCKERFILE = """\
FROM node:22-bookworm-slim

ENV NODE_ENV=development
ENV NPM_CONFIG_UPDATE_NOTIFIER=false

RUN useradd --create-home --shell /bin/bash runner

WORKDIR /workspace

COPY package.json ./

RUN npm install --omit=optional && \\
    npm cache clean --force && \\
    chown -R runner:runner /workspace

USER runner

CMD ["tail", "-f", "/dev/null"]
"""

def generate_requirements_txt(dependencies: list[dict]) -> str:
    """Produces requirements.txt content pinning each dependency exactly."""
    lines = []
    for dep in dependencies:
        name = dep.get("name")
        version = dep.get("version")
        if name and version:
            lines.append(f"{name}=={version}")
    
    if not lines:
        return "# No dependencies specified\n"
        
    return "\n".join(lines) + "\n"

def generate_package_json(dependencies: list[dict], image_name: str) -> str:
    """Produces a minimal valid package.json string."""
    sanitized_name = re.sub(r'[^a-z0-9-]', '', image_name.lower()) or "patchwork-node-env"
    
    deps_dict = {}
    for dep in dependencies:
        name = dep.get("name")
        version = dep.get("version")
        if name and version:
            deps_dict[name] = version
            
    package_json = {
        "name": sanitized_name,
        "version": "1.0.0",
        "description": f"Auto-generated environment for {image_name}",
        "dependencies": deps_dict
    }
    
    return json.dumps(package_json, indent=2)

def get_baseline_dockerfile(runtime: str) -> str:
    """Returns the matching baseline template based on runtime."""
    if runtime == "python":
        return PYTHON_BASELINE_DOCKERFILE
    elif runtime == "node":
        return NODE_BASELINE_DOCKERFILE
    else:
        raise ValueError(f"Unknown runtime: {runtime}")

def generate_build_context(image_def: dict) -> dict:
    """
    Given an image definition, returns the full set of files and templates 
    needed to build the Docker image. This is pure string generation.
    """
    runtime = image_def.get("runtime")
    name = image_def.get("name", "unknown")
    dependencies = image_def.get("dependencies", [])
    
    dockerfile_content = get_baseline_dockerfile(runtime)
    
    if runtime == "python":
        dep_file_name = "requirements.txt"
        dep_file_content = generate_requirements_txt(dependencies)
    elif runtime == "node":
        dep_file_name = "package.json"
        dep_file_content = generate_package_json(dependencies, name)
    else:
        raise ValueError(f"Unknown runtime: {runtime}")
        
    return {
        "dockerfile": dockerfile_content,
        "dependency_file_name": dep_file_name,
        "dependency_file_content": dep_file_content,
        "extra_files": {}
    }

def compute_image_content_hash(image_def: dict) -> str:
    """
    Computes a deterministic hash based on the definition's runtime and dependencies.
    Order of dependencies does not affect the hash.
    """
    runtime = image_def.get("runtime")
    dependencies = image_def.get("dependencies", [])
    
    # Sort dependencies by name to ensure consistent hashing
    sorted_deps = sorted(dependencies, key=lambda x: x.get("name", ""))
    
    hash_payload = {
        "runtime": runtime,
        "dependencies": sorted_deps
    }
    
    json_str = json.dumps(hash_payload, sort_keys=True)
    return hashlib.sha256(json_str.encode('utf-8')).hexdigest()[:12]

def build_image(image_def: dict) -> dict:
    """
    Orchestrates the full Docker build process using the Docker SDK.
    """
    client = docker.from_env()
    
    # 1. Generate build context
    context_data = generate_build_context(image_def)
    
    name = image_def.get("name", "unknown")
    sanitized_name = re.sub(r'[^a-z0-9-]', '', name.lower()) or "patchwork-image"
    content_hash = compute_image_content_hash(image_def)
    image_tag = f"patchwork/{sanitized_name}:{content_hash}"
    
    # Check if image already exists to avoid redundant rebuilds
    try:
        client.images.get(image_tag)
        return {
            "success": True,
            "image_tag": image_tag,
            "log": "Build skipped: An identical image with this tag already exists based on the content hash.\\n"
        }
    except docker.errors.ImageNotFound:
        pass
    except Exception as e:
        return {
            "success": False,
            "image_tag": None,
            "log": f"Error checking for existing image: {str(e)}\\n"
        }
    
    # 2. Create a temporary build directory
    temp_dir = tempfile.mkdtemp(prefix="patchwork_build_")
    
    build_log = ""
    success = False
    
    try:
        # 3. Write files to temp directory
        dockerfile_path = os.path.join(temp_dir, "Dockerfile")
        with open(dockerfile_path, "w", encoding="utf-8") as f:
            f.write(context_data["dockerfile"])
            
        dep_file_name = context_data.get("dependency_file_name")
        if dep_file_name:
            dep_file_path = os.path.join(temp_dir, dep_file_name)
            with open(dep_file_path, "w", encoding="utf-8") as f:
                f.write(context_data.get("dependency_file_content", ""))
            
        for extra_name, extra_content in context_data.get("extra_files", {}).items():
            extra_path = os.path.join(temp_dir, extra_name)
            with open(extra_path, "w", encoding="utf-8") as f:
                f.write(extra_content)
        
        # 4. Use Docker SDK to build
        image, logs_iterator = client.images.build(
            path=temp_dir,
            tag=image_tag,
            rm=True,
            timeout=600  # 10 minutes timeout
        )
        
        for chunk in logs_iterator:
            if 'stream' in chunk:
                build_log += chunk['stream']
            elif 'error' in chunk:
                build_log += chunk['error']
        
        success = True
        
    except docker.errors.BuildError as e:
        for chunk in e.build_log:
            if 'stream' in chunk:
                build_log += chunk['stream']
            elif 'error' in chunk:
                build_log += chunk['error']
        success = False
        image_tag = None
    except Exception as e:
        build_log += f"\\nUnexpected error during build: {str(e)}\\n"
        success = False
        image_tag = None
    finally:
        # 6. Clean up temp directory
        shutil.rmtree(temp_dir, ignore_errors=True)
        
    # 7. Return result
    return {
        "success": success,
        "image_tag": image_tag,
        "log": build_log
    }

