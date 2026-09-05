from uuid import UUID
from pydantic import BaseModel, Field, HttpUrl
from typing import Literal

class SourceFile(BaseModel):
    filename: str
    content: str

class ExecutionLimits(BaseModel):
    timeout_seconds: int = 10
    memory_mb: int = 256
    pids_limit: int = 256

class ExecuteRequest(BaseModel):
    idempotency_key: UUID
    image_tag: str
    base_ref: str | None = None
    files: list[SourceFile] = Field(min_length=1)
    test_command: str
    dependencies: list[str] = []
    limits: ExecutionLimits = ExecutionLimits()
    callback_url: HttpUrl | None = None
    metadata: dict = {}

class ExecuteResponse(BaseModel):
    job_id: str
    status: Literal["queued"]
    poll_url: str

class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "timeout"]
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    execution_time_ms: int | None = None
    metadata: dict = {}