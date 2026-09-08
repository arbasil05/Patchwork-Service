from pydantic import BaseModel
from typing import Literal, Optional, List

class ImageDefCreate(BaseModel):
    name: str
    runtime: Literal["python", "node"]

class DependencyAdd(BaseModel):
    """Request model for adding a dependency — version is optional (defaults to latest)."""
    name: str
    version: Optional[str] = None

class Dependency(BaseModel):
    """Stored/output model — version is always present after resolution."""
    name: str
    version: str

class ImageDefOut(BaseModel):
    name: str
    runtime: Literal["python", "node"]
    dependencies: List[Dependency] = []
    status: Literal["draft", "building", "ready", "failed"] | str = "draft"
    image_tag: Optional[str] = None
    build_log: Optional[str] = None
    created_at: str
    updated_at: str
