"""Single source of truth for test framework packages pinned in patchwork_service images."""
import re

FIXED_FRAMEWORKS = {
    "python": [
        {"name": "pytest", "version": "8.4.1"}
    ],
    "node": [
        {"name": "jest", "version": "29.7.0"}
    ]
}

def normalize_python_pkg_name(name: str) -> str:
    """PEP 503 normalization: lowercase and collapse runs of -_. into single -"""
    return re.sub(r"[-_.]+", "-", name.strip().lower())

def normalize_node_pkg_name(name: str) -> str:
    """NPM package name normalization."""
    return name.strip().lower()

def get_fixed_dependencies(runtime: str) -> list[dict]:
    """Returns a copy of the fixed dependencies for a runtime."""
    return [dict(d) for d in FIXED_FRAMEWORKS.get(runtime, [])]

def is_reserved_package(runtime: str, package_name: str) -> bool:
    """Checks if a given package name conflicts with a fixed framework."""
    if not package_name or runtime not in FIXED_FRAMEWORKS:
        return False
    
    if runtime == "python":
        target = normalize_python_pkg_name(package_name)
        for fixed in FIXED_FRAMEWORKS["python"]:
            if normalize_python_pkg_name(fixed["name"]) == target:
                return True
    elif runtime == "node":
        target = normalize_node_pkg_name(package_name)
        for fixed in FIXED_FRAMEWORKS["node"]:
            if normalize_node_pkg_name(fixed["name"]) == target:
                return True
    return False

def get_reserved_message(runtime: str, package_name: str) -> str:
    """Generates the rejection error message for an admin adding a reserved package."""
    fixed_deps = FIXED_FRAMEWORKS.get(runtime, [])
    if fixed_deps:
        first = fixed_deps[0]
        name = first["name"]
        version = first["version"]
        rt_label = "Python" if runtime == "python" else "Node"
        return f"{name} {version} is pre-installed in every {rt_label} image and cannot be added"
    return f"{package_name} is a reserved package and cannot be added"
