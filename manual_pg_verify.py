#!/usr/bin/env python3
"""Manual verification script for PostgreSQL ETAPA 1."""
import json
import sys
import urllib.request
import urllib.parse
import urllib.error
from http.cookiejar import CookieJar

BASE_URL = "http://127.0.0.1:8012"
cookie_jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

def request(method, path, data=None, headers=None, expect_status=200):
    """Make HTTP request and return (status, body, headers)."""
    url = BASE_URL + path
    req_headers = headers or {}
    
    if data is not None:
        if isinstance(data, dict):
            # Form data
            data = urllib.parse.urlencode(data).encode('utf-8')
            req_headers['Content-Type'] = 'application/x-www-form-urlencoded'
        elif isinstance(data, str):
            data = data.encode('utf-8')
    
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    
    try:
        resp = opener.open(req)
        body = resp.read().decode('utf-8')
        return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8') if e.fp else ""
        return e.code, body, dict(e.headers)
    except Exception as e:
        print(f"ERROR: {method} {path} - {e}")
        return None, str(e), {}

def test_status_endpoint():
    """a) GET /api/public/status -> 200, database.engine=postgres, connected=true."""
    print("\n=== Test a) /api/public/status ===")
    status, body, _ = request("GET", "/api/public/status")
    
    if status != 200:
        print(f"❌ FAIL: Expected 200, got {status}")
        return False
    
    try:
        data = json.loads(body)
    except:
        print(f"❌ FAIL: Invalid JSON response")
        return False
    
    # Check required fields
    checks = [
        (data.get("server") == "online", "server='online'"),
        (data.get("status") == "ok", "status='ok'"),
        (data.get("database", {}).get("engine") == "postgres", "database.engine='postgres'"),
        (data.get("database", {}).get("connected") == True, "database.connected=true"),
        (data.get("database", {}).get("schema_version") == 1, "database.schema_version=1"),
    ]
    
    # Check no sensitive data
    body_lower = body.lower()
    sensitive_checks = [
        ("password" not in body_lower, "no 'password' in response"),
        ("host" not in body_lower or "127.0.0.1" not in body, "no host details in response"),
    ]
    
    all_ok = True
    for check, desc in checks:
        if check:
            print(f"✅ {desc}")
        else:
            print(f"❌ {desc}")
            all_ok = False
    
    for check, desc in sensitive_checks:
        if check:
            print(f"✅ {desc}")
        else:
            print(f"⚠️  {desc}")
    
    print(f"Response: {json.dumps(data, indent=2)}")
    return all_ok

def test_health_and_map():
    """b) GET /api/public/health -> {ok:true}; GET /api/public/map -> 200."""
    print("\n=== Test b) /api/public/health and /api/public/map ===")
    
    # Health
    status, body, _ = request("GET", "/api/public/health")
    if status != 200:
        print(f"❌ /api/public/health: Expected 200, got {status}")
        return False
    
    try:
        data = json.loads(body)
        if data.get("ok") == True:
            print(f"✅ /api/public/health -> {{ok: true}}")
        else:
            print(f"❌ /api/public/health: Expected {{ok: true}}, got {data}")
            return False
    except:
        print(f"❌ /api/public/health: Invalid JSON")
        return False
    
    # Map
    status, body, _ = request("GET", "/api/public/map")
    if status == 200:
        print(f"✅ /api/public/map -> 200")
        return True
    else:
        print(f"❌ /api/public/map: Expected 200, got {status}")
        return False

def test_citizen_registration():
    """c) POST /api/public/account/register, then GET /api/public/account/me."""
    print("\n=== Test c) Citizen registration and session ===")
    
    import time
    email = f"test{int(time.time())}@example.com"
    
    # Register
    reg_data = json.dumps({
        "email": email,
        "password": "parola-lunga-123"
    }).encode('utf-8')
    
    status, body, headers = request("POST", "/api/public/account/register", 
                                   data=reg_data,
                                   headers={"Content-Type": "application/json"})
    
    if status != 200:
        print(f"❌ Registration failed: {status} - {body}")
        return False
    
    print(f"✅ Registration successful: {status}")
    
    # Check session
    status, body, _ = request("GET", "/api/public/account/me")
    
    if status != 200:
        print(f"❌ /api/public/account/me failed: {status}")
        return False
    
    try:
        data = json.loads(body)
        if data.get("email") == email:
            print(f"✅ Session valid, email correct: {data.get('email')}")
            return True
        else:
            print(f"❌ Email mismatch: expected {email}, got {data.get('email')}")
            return False
    except:
        print(f"❌ Invalid JSON response")
        return False

def test_admin_login():
    """d) POST /admin/access/login, then change password."""
    print("\n=== Test d) Admin login and password change ===")
    
    # Login
    login_data = {
        "username": "admin",
        "password": "admin123",
        "source_id": "cimitir"
    }
    
    status, body, headers = request("POST", "/admin/access/login", data=login_data)
    
    # Should redirect (303 or 302) or show password change page (200)
    if status not in (200, 302, 303):
        print(f"❌ Login failed: Expected 200/303/302, got {status}")
        print(f"Response: {body[:200]}")
        return False
    
    if status == 200 and "Schimbă parola" in body:
        print(f"✅ Admin login successful: {status} (password change required)")
    else:
        print(f"✅ Admin login successful: {status}")
    
    # Change password
    pwd_data = {
        "old_password": "admin123",
        "new_password": "Parola-test-987",
        "confirm_password": "Parola-test-987"
    }
    
    status, body, _ = request("POST", "/admin/schimba-parola", data=pwd_data)
    
    if status in (200, 302, 303):
        print(f"✅ Password change successful: {status}")
    else:
        print(f"❌ Password change failed: {status}")
        print(f"Response: {body[:200]}")
        return False
    
    # Access admin page
    status, body, _ = request("GET", "/admin/")
    
    if status == 200:
        print(f"✅ Admin dashboard accessible: {status}")
        return True
    else:
        print(f"❌ Admin dashboard failed: {status}")
        return False

def main():
    print("=" * 60)
    print("PostgreSQL ETAPA 1 - Manual Verification")
    print("=" * 60)
    
    results = []
    
    results.append(("Status endpoint", test_status_endpoint()))
    results.append(("Health & Map", test_health_and_map()))
    results.append(("Citizen registration", test_citizen_registration()))
    results.append(("Admin login", test_admin_login()))
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
    
    all_passed = all(r[1] for r in results)
    print("\n" + ("=" * 60))
    if all_passed:
        print("✅ ALL MANUAL TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
    print("=" * 60)
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
