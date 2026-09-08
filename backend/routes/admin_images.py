from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from schema.imageSchema import ImageDefCreate, ImageDefOut
from dependencies.auth import get_current_admin
from storage.image_defs import (
    save_image_def, 
    get_image_def, 
    list_image_defs, 
    delete_image_def
)

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
