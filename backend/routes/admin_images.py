import urllib.parse
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from schema.imageSchema import ImageDefCreate, ImageDefOut, DependencyAdd
from schema.validators import NPM_NAME_REGEX, PYPI_NAME_REGEX
from dependencies.auth import get_current_admin
from storage.image_defs import (
    save_image_def, 
    get_image_def, 
    list_image_defs, 
    delete_image_def,
    update_image_def
)
from services.npm_lookup import search_npm_package, NpmLookupError
from services.pypi_lookup import search_pypi_package, PypiLookupError
from services.image_build import generate_build_context

router = APIRouter(prefix="/admin/images", tags=["admin_images"])

@router.post("", response_model=ImageDefOut, status_code=status.HTTP_201_CREATED)
def create_image_def(image_def: ImageDefCreate, admin: str = Depends(get_current_admin)):
    existing = get_image_def(image_def.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Image definition '{image_def.name}' already exists"
        )
    
    try:
        data = image_def.model_dump()
        saved = save_image_def(data)
        return saved
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("", response_model=List[ImageDefOut])
def get_all_image_defs(admin: str = Depends(get_current_admin)):
    return list_image_defs()

@router.get("/{name}", response_model=ImageDefOut)
def get_image_def_by_name(name: str, admin: str = Depends(get_current_admin)):
    image_def = get_image_def(name)
    if not image_def:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Image definition '{name}' not found"
        )
    return image_def

@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def remove_image_def(name: str, admin: str = Depends(get_current_admin)):
    success = delete_image_def(name)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Image definition '{name}' not found"
        )
    return None

@router.post("/{name}/dependencies", response_model=ImageDefOut)
async def add_dependency(name: str, dependency: DependencyAdd, admin: str = Depends(get_current_admin)):
    image_def = get_image_def(name)
    if not image_def:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Image definition '{name}' not found"
        )
        
    runtime = image_def.get("runtime")
    pkg_name = dependency.name
    pkg_version = dependency.version
    version_was_explicit = pkg_version is not None
    
    # 1. Validate package name format and look up in registry
    if runtime == "node":
        if not NPM_NAME_REGEX.match(pkg_name):
            raise HTTPException(status_code=400, detail="Invalid NPM package name format.")
        try:
            lookup_res = await search_npm_package(pkg_name)
        except NpmLookupError as e:
            raise HTTPException(status_code=502, detail=f"Lookup service error: {str(e)}")
            
    elif runtime == "python":
        if not PYPI_NAME_REGEX.match(pkg_name):
            raise HTTPException(status_code=400, detail="Invalid PyPI package name format.")
        try:
            lookup_res = await search_pypi_package(pkg_name)
        except PypiLookupError as e:
            raise HTTPException(status_code=502, detail=f"Lookup service error: {str(e)}")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown runtime '{runtime}'")
        
    # 2. Check if package exists in registry
    if not lookup_res.get("exists"):
        raise HTTPException(
            status_code=400, 
            detail=f"Package '{pkg_name}' does not exist in the {runtime} registry."
        )
        
    canonical_name = lookup_res.get("name", pkg_name)
        
    # 3. Resolve version if not provided — trust the registry's latest_version directly
    if not pkg_version:
        pkg_version = lookup_res.get("latest_version")
        if not pkg_version:
            versions = lookup_res.get("versions", [])
            if versions:
                pkg_version = versions[0]
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not determine a valid version for package '{pkg_name}'"
                )
                
    # 4. Validate explicitly-specified versions against the capped list;
    #    auto-resolved latest_version is trusted directly from the registry.
    if version_was_explicit and pkg_version not in lookup_res.get("versions", []):
        raise HTTPException(
            status_code=400, 
            detail=f"Version '{pkg_version}' not found for package '{pkg_name}' in the last 20 releases."
        )
        
    # 5. Upsert dependency
    deps = image_def.get("dependencies", [])
    found = False
    for i, d in enumerate(deps):
        # Match case-insensitively to replace any existing mis-cased entry
        if d.get("name", "").lower() == canonical_name.lower() or d.get("name", "").lower() == pkg_name.lower():
            deps[i] = {"name": canonical_name, "version": pkg_version}
            found = True
            break
            
    if not found:
        deps.append({"name": canonical_name, "version": pkg_version})
        
    # 6. Save updated definition
    updated = update_image_def(name, {"dependencies": deps})
    return updated

@router.delete("/{name}/dependencies/{package_name:path}", response_model=ImageDefOut)
def remove_dependency(name: str, package_name: str, admin: str = Depends(get_current_admin)):
    image_def = get_image_def(name)
    if not image_def:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Image definition '{name}' not found"
        )
        
    pkg_name = urllib.parse.unquote(package_name)
    runtime = image_def.get("runtime")
    
    # Validate package name
    if runtime == "node" and not NPM_NAME_REGEX.match(pkg_name):
        raise HTTPException(status_code=400, detail="Invalid NPM package name format.")
    elif runtime == "python" and not PYPI_NAME_REGEX.match(pkg_name):
        raise HTTPException(status_code=400, detail="Invalid PyPI package name format.")
        
    deps = image_def.get("dependencies", [])
    filtered_deps = [d for d in deps if d.get("name") != pkg_name]
    
    if len(filtered_deps) == len(deps):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dependency '{pkg_name}' not found in image '{name}'"
        )
        
    updated = update_image_def(name, {"dependencies": filtered_deps})
    return updated

@router.get("/{name}/preview-build")
def preview_image_build(name: str, admin: str = Depends(get_current_admin)):
    image_def = get_image_def(name)
    if not image_def:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Image definition '{name}' not found"
        )
        
    try:
        context = generate_build_context(image_def)
        return context
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
