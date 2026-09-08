import os
import shutil
import sys

# Ensure backend directory is in path so we can import storage
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from storage.image_defs import (
    save_image_def, get_image_def, list_image_defs, update_image_def, delete_image_def, STORAGE_DIR, get_storage_dir
)

def run_tests():
    print("--- Starting Image Defs Storage Tests ---")
    
    # Ensure directory exists before trying to delete it or use it
    get_storage_dir()
    
    # Clean up before tests
    if os.path.exists(STORAGE_DIR):
        shutil.rmtree(STORAGE_DIR)
        os.makedirs(STORAGE_DIR)
        
    total_tests = 0
    passed_tests = 0
    
    def assert_test(name, condition):
        nonlocal total_tests, passed_tests
        total_tests += 1
        if condition:
            print(f"✅ PASS: {name}")
            passed_tests += 1
        else:
            print(f"❌ FAIL: {name}")

    # 1. Create a fake image definition and save it
    fake_def = {
        "name": "test-image-1",
        "runtime": "python",
        "dependencies": [{"name": "requests", "version": "2.31.0"}]
    }
    
    saved_def = save_image_def(fake_def)
    assert_test("Save valid definition", 
                saved_def["name"] == "test-image-1" and 
                "created_at" in saved_def and 
                "updated_at" in saved_def)
                
    # 2. Lists all definitions and confirms the new one appears
    all_defs = list_image_defs()
    assert_test("List definitions", 
                len(all_defs) == 1 and all_defs[0]["name"] == "test-image-1")
                
    # 3. Reads it back by name and confirms the data matches
    read_def = get_image_def("test-image-1")
    assert_test("Read definition by name", 
                read_def is not None and read_def["runtime"] == "python")
                
    # 4. Updates it and confirms the change persisted
    updated_def = update_image_def("test-image-1", {
        "status": "building",
        "dependencies": [{"name": "requests", "version": "2.31.0"}, {"name": "flask", "version": "3.0.0"}]
    })
    read_back_update = get_image_def("test-image-1")
    assert_test("Update definition", 
                read_back_update["status"] == "building" and 
                len(read_back_update["dependencies"]) == 2 and
                read_back_update["updated_at"] >= read_back_update["created_at"])
                
    # 5. Attempts to save a definition with an invalid name and confirms it's rejected
    try:
        save_image_def({"name": "../test-image", "runtime": "python"})
        assert_test("Reject invalid name (../)", False)
    except ValueError as e:
        assert_test("Reject invalid name (../)", True)
        
    try:
        save_image_def({"name": "test/image", "runtime": "python"})
        assert_test("Reject invalid name (slash)", False)
    except ValueError as e:
        assert_test("Reject invalid name (slash)", True)

    # 6. Attempts to save a definition with an invalid runtime and confirms it's rejected
    try:
        save_image_def({"name": "test-image-2", "runtime": "go"})
        assert_test("Reject invalid runtime", False)
    except ValueError as e:
        assert_test("Reject invalid runtime", True)
        
    # 7. Deletes it and confirms it's gone
    delete_result = delete_image_def("test-image-1")
    assert_test("Delete definition returns True", delete_result)
    
    read_deleted = get_image_def("test-image-1")
    assert_test("Get deleted definition returns None", read_deleted is None)
    
    all_defs_after_delete = list_image_defs()
    assert_test("List after delete is empty", len(all_defs_after_delete) == 0)

    print(f"--- Tests Complete: {passed_tests}/{total_tests} Passed ---")

if __name__ == "__main__":
    run_tests()
