import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from storage.image_defs import get_image_def, update_image_def
from services.image_build import build_image
import logging
import docker

def run_image_build(image_name: str):
    logging.info(f"Starting build job for image: {image_name}")
    image_def = get_image_def(image_name)
    if not image_def:
        logging.error(f"Image definition '{image_name}' not found")
        return

    old_image_tag = image_def.get("image_tag")

    # Mark as building before we touch Docker so the handler guard works.
    image_def = update_image_def(image_name, {"status": "building"})

    try:
        # Run build — this is the long-running blocking call.
        build_result = build_image(image_def)

        # Prepare update data
        update_data = {
            "status": "success" if build_result["success"] else "failed",
            "build_log": build_result["log"]
        }

        if build_result["success"]:
            new_image_tag = build_result["image_tag"]
            update_data["image_tag"] = new_image_tag

            # Cleanup old image if the tag changed
            if old_image_tag and old_image_tag != new_image_tag:
                try:
                    client = docker.from_env()
                    client.images.remove(old_image_tag, force=False)
                    logging.info(f"Successfully removed old image tag: {old_image_tag}")
                except docker.errors.APIError as e:
                    logging.warning(f"Could not remove old image tag {old_image_tag}: {e}")
                except Exception as e:
                    logging.warning(f"Unexpected error removing old image tag {old_image_tag}: {e}")

        update_image_def(image_name, update_data)
        logging.info(
            f"Finished build job for image: {image_name} "
            f"with status {update_data['status']}"
        )

    except BaseException as exc:
        # Catches rq.timeouts.JobTimeoutException (BaseException subclass),
        # KeyboardInterrupt, and any other fatal signal so the definition is
        # never left permanently stuck in "building" or "queued".
        error_msg = f"{type(exc).__name__}: {exc}"
        logging.error(
            f"Build job for '{image_name}' terminated unexpectedly: {error_msg}"
        )
        try:
            update_image_def(
                image_name,
                {"status": "failed", "build_log": error_msg},
            )
        except Exception as inner:
            logging.error(
                f"Could not write failure status for '{image_name}': {inner}"
            )
        # Re-raise so RQ still records the job as failed in Redis
        raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print(f"Usage: python {os.path.basename(__file__)} <image_name>")
        sys.exit(1)

    run_image_build(sys.argv[1])
