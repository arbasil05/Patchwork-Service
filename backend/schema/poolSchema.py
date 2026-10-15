from pydantic import BaseModel, Field
from typing import Optional

class PoolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    image_tag: str = Field(..., min_length=1)
    max_containers: int = Field(..., ge=0, le=10)

class PoolPatch(BaseModel):
    max_containers: int = Field(..., ge=0, le=10)

class PoolOut(BaseModel):
    name: str
    image_tag: str
    max_containers: int
    total: int
    free: int
    occupied: int
    is_default: bool = False
    created_at: str
    updated_at: str

class AvailableImage(BaseModel):
    image_tag: str
    display_name: str
    runtime: Optional[str] = None
    is_default: bool = False
    already_pooled: bool = False
