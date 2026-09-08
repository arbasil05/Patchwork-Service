import requests
import sys

BASE_URL = "http://127.0.0.1:8000"
ADMIN_USER = "admin"
ADMIN_PASS = "admin" # Assumes default dev env password, user can change if needed

def run_tests():
    print(f"Testing against {BASE_URL}...")
    
    session = requests.Session()
    
    # 0. Login to get session cookie
    print("\n--- 0. Logging in ---")
    resp = session.post(f"{BASE_URL}/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    if resp.status_code != 200:
        print(f"Failed to login. Is the server running and credentials correct? Status: {resp.status_code}")
        print("Set ADMIN_USERNAME=admin and ADMIN_PASSWORD_HASH in backend/.env to match your password.")
        sys.exit(1)
    print("Login successful.")

    # 1. Create a definition
    print("\n--- 1. Create definition ---")
    payload = {"name": "test-api-image", "runtime": "python"}
    resp = session.post(f"{BASE_URL}/api/admin/images", json=payload)
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}. Response: {resp.text}"
    print("✅ Created 'test-api-image' definition (201)")

    # 2. Try to create the same name again
    print("\n--- 2. Create duplicate definition ---")
    resp = session.post(f"{BASE_URL}/api/admin/images", json=payload)
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}. Response: {resp.text}"
    print("✅ Duplicate creation rejected with 409")

    # 3. List all definitions
    print("\n--- 3. List all definitions ---")
    resp = session.get(f"{BASE_URL}/api/admin/images")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}. Response: {resp.text}"
    data = resp.json()
    assert any(d.get("name") == "test-api-image" for d in data), "New definition not found in list"
    print("✅ Listed definitions, 'test-api-image' is present (200)")

    # 4. Get by name
    print("\n--- 4. Get definition by name ---")
    resp = session.get(f"{BASE_URL}/api/admin/images/test-api-image")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}. Response: {resp.text}"
    data = resp.json()
    assert data["name"] == "test-api-image", "Name mismatch"
    assert data["runtime"] == "python", "Runtime mismatch"
    print("✅ Retrieved definition correctly (200)")

    # 5. Get nonexistent name
    print("\n--- 5. Get nonexistent definition ---")
    resp = session.get(f"{BASE_URL}/api/admin/images/does-not-exist")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}. Response: {resp.text}"
    print("✅ Nonexistent definition returned 404")

    # 6. Delete definition
    print("\n--- 6. Delete definition ---")
    resp = session.delete(f"{BASE_URL}/api/admin/images/test-api-image")
    assert resp.status_code == 204, f"Expected 204, got {resp.status_code}. Response: {resp.text}"
    print("✅ Deleted definition successfully (204)")

    # 7. Get it again
    print("\n--- 7. Get deleted definition ---")
    resp = session.get(f"{BASE_URL}/api/admin/images/test-api-image")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}. Response: {resp.text}"
    print("✅ Deleted definition no longer exists (404)")

    # 8. Try flow without valid session
    print("\n--- 8. Try without session cookie ---")
    no_auth_session = requests.Session()
    
    resp1 = no_auth_session.post(f"{BASE_URL}/api/admin/images", json=payload)
    assert resp1.status_code == 401, f"Expected 401, got {resp1.status_code}"
    
    resp2 = no_auth_session.get(f"{BASE_URL}/api/admin/images")
    assert resp2.status_code == 401, f"Expected 401, got {resp2.status_code}"
    
    resp3 = no_auth_session.get(f"{BASE_URL}/api/admin/images/test-api-image")
    assert resp3.status_code == 401, f"Expected 401, got {resp3.status_code}"
    
    resp4 = no_auth_session.delete(f"{BASE_URL}/api/admin/images/test-api-image")
    assert resp4.status_code == 401, f"Expected 401, got {resp4.status_code}"
    
    print("✅ All unauthenticated requests rejected with 401")
    
    print("\n--- ALL TESTS PASSED ---")

if __name__ == "__main__":
    run_tests()
