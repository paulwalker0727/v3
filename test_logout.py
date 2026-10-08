#!/usr/bin/env python3
"""
Test secure logout flow for Cimitir FastAPI app
Tests session revocation, cookie clearing, and multi-session independence
"""
import requests
import sys
from typing import Optional, Tuple

# Configuration
# Note: External URL doesn't route /admin/* paths, only /api/*
# Testing on localhost:8001 where backend is directly accessible
BASE_URL = "http://localhost:8001"
COOKIE_NAME = "cim_session"

# Default credentials
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"
NEW_PASSWORD = "Parola-test-987"

# Color codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

class TestResults:
    def __init__(self):
        self.passed = []
        self.failed = []
        self.warnings = []
    
    def add_pass(self, test: str):
        self.passed.append(test)
        print(f"{GREEN}✓ PASS: {test}{RESET}")
    
    def add_fail(self, test: str, reason: str):
        self.failed.append(f"{test}: {reason}")
        print(f"{RED}✗ FAIL: {test}: {reason}{RESET}")
    
    def add_warning(self, msg: str):
        self.warnings.append(msg)
        print(f"{YELLOW}⚠ WARNING: {msg}{RESET}")
    
    def summary(self):
        print(f"\n{BLUE}{'='*80}{RESET}")
        print(f"{BLUE}TEST SUMMARY{RESET}")
        print(f"{BLUE}{'='*80}{RESET}")
        print(f"{GREEN}Passed: {len(self.passed)}{RESET}")
        print(f"{RED}Failed: {len(self.failed)}{RESET}")
        print(f"{YELLOW}Warnings: {len(self.warnings)}{RESET}")
        
        if self.failed:
            print(f"\n{RED}Failed tests:{RESET}")
            for fail in self.failed:
                print(f"  {RED}✗ {fail}{RESET}")
        
        if self.warnings:
            print(f"\n{YELLOW}Warnings:{RESET}")
            for warn in self.warnings:
                print(f"  {YELLOW}⚠ {warn}{RESET}")
        
        return len(self.failed) == 0

results = TestResults()

def print_section(title: str):
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}{title}{RESET}")
    print(f"{BLUE}{'='*80}{RESET}")

def print_info(msg: str):
    print(f"{YELLOW}ℹ {msg}{RESET}")

def login_and_change_password(session: requests.Session, username: str, old_password: str, new_password: str) -> Tuple[bool, str]:
    """
    Login and change password if needed.
    Returns (success, message)
    """
    # Step 1: Login
    print_info(f"Logging in as {username}...")
    resp = session.post(
        f"{BASE_URL}/admin/access/login",
        data={
            "source_id": "cimitir",
            "username": username,
            "password": old_password
        },
        allow_redirects=False,
        headers={
            "Origin": BASE_URL,
            "Sec-Fetch-Site": "same-origin"
        }
    )
    
    print_info(f"Login response: {resp.status_code}")
    
    if resp.status_code != 303:
        return False, f"Login failed with status {resp.status_code}"
    
    location = resp.headers.get("Location", "")
    print_info(f"Redirect location: {location}")
    
    # Check if password change is required
    if "/admin/schimba-parola" in location:
        print_info("Password change required, changing password...")
        
        # Step 2: Change password
        resp2 = session.post(
            f"{BASE_URL}/admin/schimba-parola",
            data={
                "old_password": old_password,
                "new_password": new_password,
                "confirm_password": new_password
            },
            allow_redirects=False,
            headers={
                "Origin": BASE_URL,
                "Sec-Fetch-Site": "same-origin"
            }
        )
        
        print_info(f"Password change response: {resp2.status_code}")
        
        if resp2.status_code != 303:
            return False, f"Password change failed with status {resp2.status_code}"
        
        location2 = resp2.headers.get("Location", "")
        if location2 != "/admin/":
            return False, f"Password change redirect unexpected: {location2}"
        
        print_info("Password changed successfully")
        return True, "Password changed"
    
    elif "/admin/" in location or location == "/admin/":
        print_info("Login successful, no password change needed")
        return True, "Already logged in"
    
    else:
        return False, f"Unexpected redirect: {location}"

def login_with_password(session: requests.Session, username: str, password: str) -> Tuple[bool, str]:
    """
    Login with given password.
    Returns (success, message)
    """
    print_info(f"Logging in as {username}...")
    resp = session.post(
        f"{BASE_URL}/admin/access/login",
        data={
            "source_id": "cimitir",
            "username": username,
            "password": password
        },
        allow_redirects=False,
        headers={
            "Origin": BASE_URL,
            "Sec-Fetch-Site": "same-origin"
        }
    )
    
    print_info(f"Login response: {resp.status_code}")
    
    if resp.status_code != 303:
        return False, f"Login failed with status {resp.status_code}"
    
    location = resp.headers.get("Location", "")
    print_info(f"Redirect location: {location}")
    
    if "/admin/" in location or location == "/admin/":
        return True, "Login successful"
    elif "/admin/schimba-parola" in location:
        return False, "Password change required (unexpected)"
    else:
        return False, f"Unexpected redirect: {location}"

def get_session_cookie(session: requests.Session) -> Optional[str]:
    """Extract the session cookie value"""
    for cookie in session.cookies:
        if cookie.name == COOKIE_NAME:
            return cookie.value
    return None

def test_scenario_1_two_sessions():
    """Scenario 1: Create two independent sessions with different cookies"""
    print_section("SCENARIO 1: Two Independent Sessions")
    
    session1 = requests.Session()
    session2 = requests.Session()
    
    # Try to login with default password first, change if needed
    success1, msg1 = login_and_change_password(session1, DEFAULT_USERNAME, DEFAULT_PASSWORD, NEW_PASSWORD)
    
    if not success1:
        # Try with new password
        success1, msg1 = login_with_password(session1, DEFAULT_USERNAME, NEW_PASSWORD)
    
    if not success1:
        results.add_fail("Session 1 login", msg1)
        return None, None
    
    cookie1 = get_session_cookie(session1)
    if not cookie1:
        results.add_fail("Session 1 cookie", "No cookie found")
        return None, None
    
    print_info(f"Session 1 cookie: {cookie1[:20]}...")
    results.add_pass("Session 1 created")
    
    # Create session 2
    success2, msg2 = login_with_password(session2, DEFAULT_USERNAME, NEW_PASSWORD)
    
    if not success2:
        results.add_fail("Session 2 login", msg2)
        return session1, None
    
    cookie2 = get_session_cookie(session2)
    if not cookie2:
        results.add_fail("Session 2 cookie", "No cookie found")
        return session1, None
    
    print_info(f"Session 2 cookie: {cookie2[:20]}...")
    
    # Verify cookies are different
    if cookie1 == cookie2:
        results.add_fail("Different cookies", "Both sessions have same cookie")
        return session1, session2
    
    results.add_pass("Session 2 created with different cookie")
    
    # Verify both can access /admin/
    resp1 = session1.get(f"{BASE_URL}/admin/", allow_redirects=False)
    if resp1.status_code == 200:
        results.add_pass("Session 1 can access /admin/")
    else:
        results.add_fail("Session 1 access /admin/", f"Status {resp1.status_code}")
    
    resp2 = session2.get(f"{BASE_URL}/admin/", allow_redirects=False)
    if resp2.status_code == 200:
        results.add_pass("Session 2 can access /admin/")
    else:
        results.add_fail("Session 2 access /admin/", f"Status {resp2.status_code}")
    
    # Verify both can access /api/admin/map.json
    resp1_api = session1.get(f"{BASE_URL}/api/admin/map.json")
    if resp1_api.status_code == 200:
        results.add_pass("Session 1 can access /api/admin/map.json")
    else:
        results.add_fail("Session 1 access API", f"Status {resp1_api.status_code}")
    
    resp2_api = session2.get(f"{BASE_URL}/api/admin/map.json")
    if resp2_api.status_code == 200:
        results.add_pass("Session 2 can access /api/admin/map.json")
    else:
        results.add_fail("Session 2 access API", f"Status {resp2_api.status_code}")
    
    return session1, session2

def test_scenario_2_logout_response(session: requests.Session):
    """Scenario 2: Logout returns 303 with cookie clearing"""
    print_section("SCENARIO 2: Logout Response")
    
    if not session:
        results.add_fail("Logout test", "No session available")
        return None
    
    old_cookie = get_session_cookie(session)
    print_info(f"Old cookie: {old_cookie[:20] if old_cookie else 'None'}...")
    
    # Logout with redirects disabled
    resp = session.get(f"{BASE_URL}/admin/logout", allow_redirects=False)
    
    print_info(f"Logout status: {resp.status_code}")
    print_info(f"Logout headers: {dict(resp.headers)}")
    
    # Check status code
    if resp.status_code == 303:
        results.add_pass("Logout returns 303")
    else:
        results.add_fail("Logout status", f"Expected 303, got {resp.status_code}")
    
    # Check redirect location
    location = resp.headers.get("Location", "")
    if location == "/admin/access":
        results.add_pass("Logout redirects to /admin/access")
    else:
        results.add_fail("Logout redirect", f"Expected /admin/access, got {location}")
    
    # Check Set-Cookie header
    set_cookie = resp.headers.get("Set-Cookie", "")
    print_info(f"Set-Cookie: {set_cookie}")
    
    if COOKIE_NAME in set_cookie:
        # Check for cookie clearing attributes
        if "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower():
            results.add_pass("Logout clears cookie (Max-Age=0 or expires)")
        else:
            results.add_fail("Cookie clearing", "No Max-Age=0 or expires in Set-Cookie")
        
        if "Path=/" in set_cookie:
            results.add_pass("Cookie has Path=/")
        else:
            results.add_warning("Cookie Path=/ not found in Set-Cookie")
        
        if "HttpOnly" in set_cookie:
            results.add_pass("Cookie has HttpOnly")
        else:
            results.add_fail("Cookie HttpOnly", "HttpOnly not in Set-Cookie")
        
        if "SameSite=lax" in set_cookie or "SameSite=Lax" in set_cookie:
            results.add_pass("Cookie has SameSite=lax")
        else:
            results.add_warning("SameSite=lax not found in Set-Cookie")
    else:
        results.add_fail("Set-Cookie header", f"Cookie {COOKIE_NAME} not in Set-Cookie")
    
    return old_cookie

def test_scenario_3_old_cookie_rejected(old_cookie: str):
    """Scenario 3: Old cookie is rejected after logout"""
    print_section("SCENARIO 3: Old Cookie Rejected After Logout")
    
    if not old_cookie:
        results.add_fail("Old cookie test", "No old cookie available")
        return
    
    # Create a new session with the old cookie
    new_session = requests.Session()
    new_session.cookies.set(COOKIE_NAME, old_cookie, domain="localhost", path="/")
    
    print_info(f"Testing with old cookie: {old_cookie[:20]}...")
    
    # Test GET /admin/
    resp1 = new_session.get(f"{BASE_URL}/admin/", allow_redirects=False)
    print_info(f"GET /admin/ status: {resp1.status_code}")
    
    if resp1.status_code in [401, 403, 303, 307]:
        results.add_pass(f"GET /admin/ rejected with {resp1.status_code}")
        if resp1.status_code in [303, 307]:
            location = resp1.headers.get("Location", "")
            if "/admin/access" in location:
                results.add_pass("GET /admin/ redirects to /admin/access")
            else:
                results.add_warning(f"Redirect location: {location}")
    else:
        results.add_fail("GET /admin/ rejection", f"Expected 401/403/303/307, got {resp1.status_code}")
    
    # Test GET /admin/system
    resp2 = new_session.get(f"{BASE_URL}/admin/system", allow_redirects=False)
    print_info(f"GET /admin/system status: {resp2.status_code}")
    
    if resp2.status_code in [401, 403, 303, 307]:
        results.add_pass(f"GET /admin/system rejected with {resp2.status_code}")
    else:
        results.add_fail("GET /admin/system rejection", f"Expected 401/403/303/307, got {resp2.status_code}")
    
    # Test GET /api/admin/map.json
    resp3 = new_session.get(f"{BASE_URL}/api/admin/map.json", allow_redirects=False)
    print_info(f"GET /api/admin/map.json status: {resp3.status_code}")
    
    if resp3.status_code in [401, 403, 303, 307]:
        results.add_pass(f"GET /api/admin/map.json rejected with {resp3.status_code}")
    else:
        results.add_fail("GET /api/admin/map.json rejection", f"Expected 401/403/303/307, got {resp3.status_code}")
    
    # Test POST (if there's a simple POST endpoint)
    # For now, we'll skip POST tests as they require more setup

def test_scenario_4_session2_still_works(session2: requests.Session):
    """Scenario 4: Session 2 still works after session 1 logout"""
    print_section("SCENARIO 4: Session 2 Still Works")
    
    if not session2:
        results.add_fail("Session 2 test", "No session 2 available")
        return
    
    # Test GET /admin/
    resp1 = session2.get(f"{BASE_URL}/admin/", allow_redirects=False)
    print_info(f"Session 2 GET /admin/ status: {resp1.status_code}")
    
    if resp1.status_code == 200:
        results.add_pass("Session 2 can still access /admin/")
    else:
        results.add_fail("Session 2 /admin/", f"Expected 200, got {resp1.status_code}")
    
    # Test GET /api/admin/map.json
    resp2 = session2.get(f"{BASE_URL}/api/admin/map.json")
    print_info(f"Session 2 GET /api/admin/map.json status: {resp2.status_code}")
    
    if resp2.status_code == 200:
        results.add_pass("Session 2 can still access /api/admin/map.json")
    else:
        results.add_fail("Session 2 API", f"Expected 200, got {resp2.status_code}")

def test_scenario_5_fresh_login():
    """Scenario 5: Fresh login after logout works"""
    print_section("SCENARIO 5: Fresh Login After Logout")
    
    fresh_session = requests.Session()
    
    success, msg = login_with_password(fresh_session, DEFAULT_USERNAME, NEW_PASSWORD)
    
    if not success:
        results.add_fail("Fresh login", msg)
        return None
    
    results.add_pass("Fresh login successful")
    
    # Verify access
    resp = fresh_session.get(f"{BASE_URL}/admin/", allow_redirects=False)
    if resp.status_code == 200:
        results.add_pass("Fresh session can access /admin/")
    else:
        results.add_fail("Fresh session access", f"Status {resp.status_code}")
    
    return fresh_session

def test_scenario_6_logout_without_cookie():
    """Scenario 6: Logout without cookie doesn't error"""
    print_section("SCENARIO 6: Logout Without Cookie")
    
    # Test with no cookie
    no_cookie_session = requests.Session()
    resp1 = no_cookie_session.get(f"{BASE_URL}/admin/logout", allow_redirects=False)
    print_info(f"Logout without cookie status: {resp1.status_code}")
    
    if resp1.status_code == 303:
        results.add_pass("Logout without cookie returns 303")
    elif resp1.status_code == 500:
        results.add_fail("Logout without cookie", "Returns 500 error")
    else:
        results.add_warning(f"Logout without cookie returns {resp1.status_code}")
    
    # Test with already-revoked cookie (use an old cookie if available)
    # For now, we'll create a fake cookie
    revoked_session = requests.Session()
    revoked_session.cookies.set(COOKIE_NAME, "fake_revoked_cookie", domain="localhost", path="/")
    resp2 = revoked_session.get(f"{BASE_URL}/admin/logout", allow_redirects=False)
    print_info(f"Logout with revoked cookie status: {resp2.status_code}")
    
    if resp2.status_code == 303:
        results.add_pass("Logout with revoked cookie returns 303")
    elif resp2.status_code == 500:
        results.add_fail("Logout with revoked cookie", "Returns 500 error")
    else:
        results.add_warning(f"Logout with revoked cookie returns {resp2.status_code}")

def test_scenario_7_repeated_logout(session: requests.Session):
    """Scenario 7: Repeated logout doesn't break"""
    print_section("SCENARIO 7: Repeated Logout")
    
    if not session:
        results.add_fail("Repeated logout", "No session available")
        return
    
    # First logout
    resp1 = session.get(f"{BASE_URL}/admin/logout", allow_redirects=False)
    print_info(f"First logout status: {resp1.status_code}")
    
    if resp1.status_code == 303:
        results.add_pass("First logout returns 303")
    else:
        results.add_fail("First logout", f"Expected 303, got {resp1.status_code}")
    
    # Second logout (same session, cookie should be cleared)
    resp2 = session.get(f"{BASE_URL}/admin/logout", allow_redirects=False)
    print_info(f"Second logout status: {resp2.status_code}")
    
    if resp2.status_code == 303:
        results.add_pass("Second logout returns 303")
    elif resp2.status_code == 500:
        results.add_fail("Second logout", "Returns 500 error")
    else:
        results.add_warning(f"Second logout returns {resp2.status_code}")

def check_backend_logs():
    """Check backend logs for errors"""
    print_section("Backend Logs Check")
    
    import subprocess
    try:
        result = subprocess.run(
            ["tail", "-n", "50", "/var/log/supervisor/backend.err.log"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            logs = result.stdout
            if logs.strip():
                print_info("Recent backend errors:")
                # Filter out sensitive info
                for line in logs.split("\n")[-20:]:
                    if line.strip() and not any(secret in line.lower() for secret in ["password", "secret", "key", "token"]):
                        print(f"  {line}")
            else:
                print_info("No recent errors in backend logs")
        else:
            print_info("Could not read backend logs")
    except Exception as e:
        print_info(f"Error reading logs: {e}")

def main():
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}Cimitir Secure Logout Flow Test{RESET}")
    print(f"{BLUE}Base URL: {BASE_URL}{RESET}")
    print(f"{BLUE}{'='*80}{RESET}")
    
    # Scenario 1: Create two sessions
    session1, session2 = test_scenario_1_two_sessions()
    
    if not session1:
        print(f"\n{RED}Cannot proceed without session 1{RESET}")
        results.summary()
        return 1
    
    # Scenario 2: Logout session 1
    old_cookie = test_scenario_2_logout_response(session1)
    
    # Scenario 3: Old cookie rejected
    if old_cookie:
        test_scenario_3_old_cookie_rejected(old_cookie)
    
    # Scenario 4: Session 2 still works
    if session2:
        test_scenario_4_session2_still_works(session2)
    
    # Scenario 5: Fresh login
    fresh_session = test_scenario_5_fresh_login()
    
    # Scenario 6: Logout without cookie
    test_scenario_6_logout_without_cookie()
    
    # Scenario 7: Repeated logout
    if fresh_session:
        test_scenario_7_repeated_logout(fresh_session)
    
    # Check backend logs
    check_backend_logs()
    
    # Summary
    success = results.summary()
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
