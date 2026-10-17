import os
import docker
import time
import tarfile
import io
import requests
import base64
from .pool.pool_manager import acquire_container, release_container, create_container, PoolSuspendedError

docker_client = docker.from_env()

def cleanup_submission_files(container, files: list) -> None:
    """
    Deletes only the files that were written for this submission.
    """
    for file_obj in files:
        filename = file_obj.get("filename")
        if filename:
            exit_code, output = container.exec_run(["rm", "-rf", f"/workspace/{filename}"])
            if exit_code != 0:
                raise RuntimeError(
                    f"Failed to clean up {filename}: "
                    f"{output.decode('utf-8', errors='replace')}"
                )

def write_files_to_container(container, files: list) -> None:
    """
    Writes each project file into the container's /workspace directory.
    Uses base64 and exec_run to bypass Docker daemon bug with put_archive on read_only rootfs.
    """
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode='w') as tar:
        for file_obj in files:
            filename = file_obj["filename"]
            content = file_obj["content"].encode('utf-8')
            tarinfo = tarfile.TarInfo(name=filename)
            tarinfo.size = len(content)
            tar.addfile(tarinfo, io.BytesIO(content))
    
    tar_b64 = base64.b64encode(tar_stream.getvalue()).decode('ascii')
    
    # We pipe the base64 string into base64 decode and directly extract with tar
    cmd = f"echo {tar_b64} | base64 -d | tar -x -C /workspace"
    exit_code, output = container.exec_run(["sh", "-c", cmd])
    
    if exit_code != 0:
        raise RuntimeError(f"Failed to extract files into container: {output.decode('utf-8', errors='replace')}")

def _dispatch_webhook(callback_url, job_id, status, result):
    if not callback_url:
        return
        
    payload = {
        "job_id": str(job_id),
        "status": status,
        "exit_code": result.get("exit_code"),
        "stdout": result.get("stdout"),
        "stderr": result.get("error") if "error" in result else result.get("stderr"),
        "metadata": result.get("metrics", {})
    }
    
    if "total_worker_time" in payload["metadata"]:
        payload["execution_time_ms"] = int(payload["metadata"]["total_worker_time"] * 1000)
    
    try:
        # Fire-and-forget
        requests.post(callback_url, json=payload, timeout=5)
    except Exception as e:
        print(f"Failed to dispatch webhook for job {job_id} to {callback_url}: {e}")

def run_submission(req_dict: dict) -> dict:
    """
    Executes a user submission securely inside a Docker container (pool or on-demand).
    """
    job_start_time = time.time()
    enqueued_at = req_dict.get("_enqueued_at")
    queue_delay = (job_start_time - enqueued_at) if enqueued_at else 0.0

    image_tag = req_dict.get("image_tag")
    files = req_dict.get("files", [])
    test_command = req_dict.get("test_command")
    limits = req_dict.get("limits", {})
    callback_url = req_dict.get("callback_url")
    job_id = req_dict.get("idempotency_key")
    metadata_in = req_dict.get("metadata", {}) or {}

    eval_mode_switch = os.getenv("EVAL_MODE_SWITCH") == "1"
    requested_mode = metadata_in.get("execution_mode", "pool")
    is_on_demand = eval_mode_switch and (requested_mode == "on_demand")
    execution_mode = "on_demand" if is_on_demand else "pool"

    metrics = {
        "queue_delay": queue_delay,
        "execution_mode": execution_mode,
        "container_acquire_time": None,
        "pool_hit": None,
        "container_create_time": None,
        "container_destroy_time": None,
    }

    timeout_seconds = limits.get("timeout_seconds", 10)

    if not image_tag or not test_command:
        result = {"error": "Missing image_tag or test_command in request.", "metrics": metrics}
        _dispatch_webhook(callback_url, job_id, "failed", result)
        return result

    container_id = None
    if is_on_demand:
        t_create_start = time.time()
        try:
            container_id = create_container(image_tag, register_in_pool=False)
            metrics["container_create_time"] = time.time() - t_create_start
            metrics["pool_hit"] = False
        except Exception as e:
            result = {"error": f"Failed to create on-demand container: {str(e)}", "metrics": metrics}
            _dispatch_webhook(callback_url, job_id, "failed", result)
            return result
    else:
        try:
            container_id, acquire_dur, pool_hit = acquire_container(image_tag, timeout_seconds=timeout_seconds, return_stats=True)
            metrics["container_acquire_time"] = acquire_dur
            metrics["pool_hit"] = pool_hit
        except PoolSuspendedError as e:
            result = {"error": f"Failed to acquire container: {str(e)}", "metrics": metrics}
            _dispatch_webhook(callback_url, job_id, "failed", result)
            return result
        except Exception as e:
            result = {"error": f"Failed to acquire container: {str(e)}", "metrics": metrics}
            _dispatch_webhook(callback_url, job_id, "failed", result)
            return result

    total_start = time.time()
    try:
        container = docker_client.containers.get(container_id)

        t0 = time.time()
        # 1. Remove any leftover submission files from a previous run
        cleanup_submission_files(container, files)

        # 2. Write project files into the container
        write_files_to_container(container, files)
        t1 = time.time()
        metrics["workspace_creation_time"] = t1 - t0

        # 3. Execute the test suite
        t2 = time.time()
        exit_code, output = container.exec_run(
            ["sh", "-c", test_command],
            workdir="/workspace"
        )
        t3 = time.time()
        metrics["execution_time"] = t3 - t2

        # 4. Remove submission files before releasing/destroying the container
        t4 = time.time()
        cleanup_submission_files(container, files)
        t5 = time.time()
        metrics["workspace_cleanup_time"] = t5 - t4
        metrics["total_worker_time"] = t5 - total_start

        result = {
            "exit_code": exit_code,
            "stdout": output.decode("utf-8", errors="replace"),
            "stderr": "",
            "metrics": metrics
        }

        status = "completed"
        _dispatch_webhook(callback_url, job_id, status, result)

        return result

    except Exception as e:
        result = {"error": f"Execution failed: {str(e)}", "metrics": metrics}
        _dispatch_webhook(callback_url, job_id, "failed", result)
        return result
    finally:
        if container_id:
            if is_on_demand:
                t_dest_0 = time.time()
                try:
                    c = docker_client.containers.get(container_id)
                    c.kill()
                except Exception:
                    pass
                metrics["container_destroy_time"] = time.time() - t_dest_0
            else:
                release_container(image_tag, container_id)