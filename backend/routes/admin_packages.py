
from fastapi import APIRouter, Depends, HTTPException, status, Query
from dependencies.auth import get_current_admin
from services.npm_lookup import search_npm_package, NpmLookupError
from services.pypi_lookup import search_pypi_package, PypiLookupError
from schema.validators import NPM_NAME_REGEX, PYPI_NAME_REGEX

router = APIRouter(prefix="/admin/packages", tags=["admin_packages"])

@router.get("/npm/search")
async def search_npm(name: str = Query(None), admin: str = Depends(get_current_admin)):
    if not name or not name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The 'name' query parameter is required and cannot be empty."
        )
        
    name = name.strip()
    
    if not NPM_NAME_REGEX.match(name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid NPM package name format."
        )
        
    try:
        result = await search_npm_package(name)
        return result
    except NpmLookupError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Lookup service error: {str(e)}"
        )

@router.get("/pypi/search")
async def search_pypi(name: str = Query(None), admin: str = Depends(get_current_admin)):
    if not name or not name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The 'name' query parameter is required and cannot be empty."
        )
        
    name = name.strip()
    
    if not PYPI_NAME_REGEX.match(name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PyPI package name format."
        )
        
    try:
        result = await search_pypi_package(name)
        return result
    except PypiLookupError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Lookup service error: {str(e)}"
        )
