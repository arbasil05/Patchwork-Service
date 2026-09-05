import docker
import tarfile
import io
import base64

docker_client = docker.from_env()

def test():
    container = docker_client.containers.run(
        "alpine", 
        command=["sh", "-c", "tail -f /dev/null"],
        detach=True,
        read_only=True,
        tmpfs={"/workspace": "exec"}
    )
    
    try:
        # Create tar
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode='w') as tar:
            content = b"hello world"
            tarinfo = tarfile.TarInfo(name="test.txt")
            tarinfo.size = len(content)
            tar.addfile(tarinfo, io.BytesIO(content))
        
        tar_data = tar_stream.getvalue()
        tar_b64 = base64.b64encode(tar_data).decode('ascii')
        
        print("Trying base64 exec...")
        cmd = f"echo {tar_b64} | base64 -d | tar -x -C /workspace"
        exit_code, out = container.exec_run(["sh", "-c", cmd])
        print(f"Exec output: {exit_code}, {out}")
        
        # Verify
        exit_code, out = container.exec_run(["cat", "/workspace/test.txt"])
        print(f"Verification: {exit_code}, {out}")
        
    finally:
        container.remove(force=True)

if __name__ == "__main__":
    test()
