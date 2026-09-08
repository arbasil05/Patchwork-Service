import json
import re

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
