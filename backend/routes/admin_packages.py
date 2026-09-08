import re
from fastapi import APIRouter, Depends, HTTPException, status, Query
from dependencies.auth import get_current_admin
from services.npm_lookup import search_npm_package, NpmLookupError

router = APIRouter(prefix="/admin/packages", tags=["admin_packages"])

# Regex for NPM package validation:
# - Can optionally start with an @scope/
# - Name can contain lowercase letters, numbers, hyphens, underscores, dots.
# - No other slashes, no spaces.
NPM_NAME_REGEX = re.compile(r"^(?:@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*$")

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
