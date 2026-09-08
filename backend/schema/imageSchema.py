from pydantic import BaseModel
from typing import Literal, Optional, List

class ImageDefCreate(BaseModel):
    name: str
    runtime: Literal["python", "node"]

class Dependency(BaseModel):
    name: str
    version: str

class ImageDefOut(BaseModel):
    name: str
    runtime: Literal["python", "node"]
    dependencies: List[Dependency] | List[dict] = []
    status: Literal["draft", "building", "ready", "failed"] | str = "draft"
    image_tag: Optional[str] = None
    build_log: Optional[str] = None
    created_at: str
    updated_at: str
