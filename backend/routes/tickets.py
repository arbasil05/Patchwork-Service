from fastapi import APIRouter, HTTPException, Depends
from schema.ticketSchema import ExecuteRequest, ExecuteResponse, JobStatusResponse
from tasks.tasks import run_submission
from dependencies.deps import verify_client
from redis.asyncio import Redis as AsyncRedis
from redis import Redis as SyncRedis
from rq import Queue
from rq.job import Job
from config.settings import settings

redis_conn = AsyncRedis(host=settings.REDIS_HOST, port=6379)
sync_redis_conn = SyncRedis(host=settings.REDIS_HOST, port=6379)
task_queue = Queue("submission_queue", connection=sync_redis_conn)

router = APIRouter(prefix="/v1", tags=["execute"])

@router.get("/health")
def health_check():
    return {"status": "ok"}

@router.post("/execute", response_model=ExecuteResponse, status_code=202)
async def submit_ticket(req: ExecuteRequest, client: dict = Depends(verify_client)):
    idempotency_key = str(req.idempotency_key)
    client_id = client.get("client_id", "anonymous")

    was_new = await redis_conn.set(f"idempotency:{idempotency_key}", "pending", ex=60*60*24*2, nx=True)

    if not was_new:
        existing_owner = await redis_conn.get(f"job_owner:{idempotency_key}")
        if existing_owner and existing_owner.decode('utf-8') != client_id:
            raise HTTPException(status_code=403, detail="Forbidden: Job belongs to another client")
        elif not existing_owner:
            raise HTTPException(status_code=403, detail="Forbidden: Job ownership missing")

        return ExecuteResponse(
            job_id=idempotency_key,
            status="queued",
            poll_url=f"/v1/status/{idempotency_key}"
        )

    await redis_conn.set(f"job_owner:{idempotency_key}", client_id, ex=60*60*24*2)

    # Convert request to dictionary, using model_dump if available (pydantic v2), else dict (v1)
    req_dict = req.model_dump(mode='json') if hasattr(req, "model_dump") else req.dict()
    
    job = task_queue.enqueue(
        run_submission,
        req_dict,
        job_id=idempotency_key
    )

    return ExecuteResponse(
        job_id=job.id,
        status="queued",
        poll_url=f"/v1/status/{job.id}"
    )

@router.get("/status/{job_id}", response_model=JobStatusResponse)
def get_status(job_id: str, client: dict = Depends(verify_client)):
    client_id = client.get("client_id", "anonymous")
    
    owner = sync_redis_conn.get(f"job_owner:{job_id}")
    if not owner or owner.decode('utf-8') != client_id:
        raise HTTPException(
            status_code=404,
            detail="job not found"
        )
        
    try:
        job = Job.fetch(job_id, connection=sync_redis_conn)
    except Exception:
        raise HTTPException(
            status_code=404,
            detail="job not found"
        )
        
    status_map = {
        "queued": "queued",
        "started": "running",
        "deferred": "queued",
        "finished": "completed",
        "stopped": "failed",
        "scheduled": "queued",
        "canceled": "failed",
        "failed": "failed"
    }
    mapped_status = status_map.get(job.get_status(), "failed")
    
    resp = JobStatusResponse(job_id=job_id, status=mapped_status)
    
    if mapped_status == "completed" and job.result:
        result = job.result
        if "error" in result:
            resp.status = "failed"
            resp.stderr = result["error"]
        else:
            resp.exit_code = result.get("exit_code")
            resp.stdout = result.get("stdout")
            resp.stderr = result.get("stderr")
            metrics = result.get("metrics", {})
            if "total_worker_time" in metrics:
                resp.execution_time_ms = int(metrics["total_worker_time"] * 1000)
            resp.metadata = metrics
            
    elif mapped_status == "failed" and job.exc_info:
        resp.stderr = str(job.exc_info)
        
    return resp
