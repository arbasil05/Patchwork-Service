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
    
    # Set status defensively
    image_def = update_image_def(image_name, {"status": "building"})
    
    # Run build
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
    logging.info(f"Finished build job for image: {image_name} with status {update_data['status']}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print(f"Usage: python {os.path.basename(__file__)} <image_name>")
        sys.exit(1)
        
    run_image_build(sys.argv[1])
