#!/usr/bin/env python3
"""
Test secure logout functionality for Cimitir FastAPI app
Tests POST /admin/logout with CSRF protection and session revocation
"""
import requests
import sys
from typing import Optional, Dict

# Configuration
BASE_URL = "http://localhost:8001"
TEST_USERNAME = "test_tmp_admin"
TEST_PASSWORD = "Nn-mNl5dJqYzjL7bgTfTw!7"

# Color codes for output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

class TestResults:
    def __init__(self):
        self.passed = []
        self.failed = []
    
    def add_pass(self, test: str):
        self.passed.append(test)
        print(f"{GREEN}✓ PASS: {test}{RESET}")
    
    def add_fail(self, test: str, reason: str):
        self.failed.append(f"{test}: {reason}")
        print(f"{RED}✗ FAIL: {test}: {reason}{RESET}")
    
    def summary(self):
        print(f"\n{BLUE}{'='*80}{RESET}")
        print(f"{BLUE}TEST SUMMARY{RESET}")
        print(f"{BLUE}{'='*80}{RESET}")
        print(f"{GREEN}Passed: {len(self.passed)}/{len(self.passed) + len(self.failed)}{RESET}")
        if self.failed:
            print(f"{RED}Failed: {len(self.failed)}{RESET}")
            print(f"\n{RED}Failed tests:{RESET}")
            for fail in self.failed:
                print(f"  {RED}✗ {fail}{RESET}")
        return len(self.failed) == 0

results = TestResults()

def print_info(msg: str):
    print(f"{YELLOW}ℹ {msg}{RESET}")

def print_section(title: str):
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}{title}{RESET}")
    print(f"{BLUE}{'='*80}{RESET}")

def login(session: requests.Session, username: str, password: str) -> bool:
    """Login and return True if successful"""
    try:
        resp = session.post(
            f"{BASE_URL}/admin/access/login",
            data={
                "source_id": "cimitir",
                "username": username,
                "password": password
            },
            allow_redirects=False
        )
        
        if resp.status_code == 303:
            # Check if we got a cookie
            if 'cim_session' in session.cookies:
                print_info(f"Login successful, got cookie: {session.cookies.get('cim_session')[:20]}...")
                return True
        
        print_info(f"Login failed: status={resp.status_code}")
        return False
    except Exception as e:
        print_info(f"Login exception: {e}")
        return False

def test_scenario_1_two_sessions():
    """Scenario 1: Create two independent sessions with different cookies"""
    print_section("Scenario 1: Two Independent Sessions")
    
    s1 = requests.Session()
    s2 = requests.Session()
    
    # Login session 1
    print_info("Creating session S1...")
    if not login(s1, TEST_USERNAME, TEST_PASSWORD):
        results.add_fail("S1 login", "Failed to login")
        return None, None
    
    # Login session 2
    print_info("Creating session S2...")
    if not login(s2, TEST_USERNAME, TEST_PASSWORD):
        results.add_fail("S2 login", "Failed to login")
        return None, None
    
    # Verify different cookies
    cookie1 = s1.cookies.get('cim_session')
    cookie2 = s2.cookies.get('cim_session')
    
    if cookie1 and cookie2 and cookie1 != cookie2:
        results.add_pass("S1 and S2 have different cookies")
    else:
        results.add_fail("Different cookies", f"S1={cookie1[:20] if cookie1 else None}, S2={cookie2[:20] if cookie2 else None}")
        return None, None
    
    # Test S1 can access /admin/
    resp = s1.get(f"{BASE_URL}/admin/", allow_redirects=False)
    if resp.status_code == 200:
        results.add_pass("S1 can access /admin/ (200)")
    else:
        results.add_fail("S1 access /admin/", f"Status {resp.status_code}")
    
    # Test S1 can access /api/admin/map.json
    resp = s1.get(f"{BASE_URL}/api/admin/map.json")
    if resp.status_code == 200:
        results.add_pass("S1 can access /api/admin/map.json (200)")
    else:
        results.add_fail("S1 access /api/admin/map.json", f"Status {resp.status_code}")
    
    # Test S2 can access /admin/
    resp = s2.get(f"{BASE_URL}/admin/", allow_redirects=False)
    if resp.status_code == 200:
        results.add_pass("S2 can access /admin/ (200)")
    else:
        results.add_fail("S2 access /admin/", f"Status {resp.status_code}")
    
    # Test S2 can access /api/admin/map.json
    resp = s2.get(f"{BASE_URL}/api/admin/map.json")
    if resp.status_code == 200:
        results.add_pass("S2 can access /api/admin/map.json (200)")
    else:
        results.add_fail("S2 access /api/admin/map.json", f"Status {resp.status_code}")
    
    return s1, s2

def test_scenario_2_capture_cookie(s1: requests.Session) -> str:
    """Scenario 2: Capture S1's cookie value as OLD_COOKIE"""
    print_section("Scenario 2: Capture OLD_COOKIE")
    
    old_cookie = s1.cookies.get('cim_session')
    if old_cookie:
        print_info(f"OLD_COOKIE captured: {old_cookie[:30]}...")
        results.add_pass("OLD_COOKIE captured")
        return old_cookie
    else:
        results.add_fail("Capture OLD_COOKIE", "No cookie found")
        return None

def test_scenario_3_get_logout(s1: requests.Session):
    """Scenario 3: GET /admin/logout should be harmless (no revocation, no cookie clearing)"""
    print_section("Scenario 3: GET /admin/logout (harmless)")
    
    # GET /admin/logout
    resp = s1.get(f"{BASE_URL}/admin/logout", allow_redirects=False)
    
    # Should return 303 redirect
    if resp.status_code == 303:
        results.add_pass("GET /admin/logout returns 303")
    else:
        results.add_fail("GET /admin/logout status", f"Expected 303, got {resp.status_code}")
    
    # Should NOT clear cookie (no Set-Cookie with Max-Age=0)
    set_cookie = resp.headers.get('Set-Cookie', '')
    if 'Max-Age=0' in set_cookie or 'expires=' in set_cookie.lower():
        results.add_fail("GET /admin/logout cookie clearing", "Cookie was cleared (should not be)")
    else:
        results.add_pass("GET /admin/logout does not clear cookie")
    
    # S1 should still work
    resp = s1.get(f"{BASE_URL}/api/admin/map.json")
    if resp.status_code == 200:
        results.add_pass("S1 still works after GET /admin/logout (200)")
    else:
        results.add_fail("S1 after GET logout", f"Status {resp.status_code}, expected 200")

def test_scenario_4_csrf_protection(s1: requests.Session):
    """Scenario 4: POST /admin/logout with cross-origin headers should return 403"""
    print_section("Scenario 4: CSRF Protection")
    
    # Test 1: Sec-Fetch-Site: cross-site
    print_info("Testing POST with Sec-Fetch-Site: cross-site")
    resp = s1.post(
        f"{BASE_URL}/admin/logout",
        headers={"Sec-Fetch-Site": "cross-site"},
        allow_redirects=False
    )
    if resp.status_code == 403:
        results.add_pass("POST with Sec-Fetch-Site: cross-site returns 403")
    else:
        results.add_fail("CSRF cross-site", f"Expected 403, got {resp.status_code}")
    
    # Test 2: Origin: https://evil.example
    print_info("Testing POST with Origin: https://evil.example")
    resp = s1.post(
        f"{BASE_URL}/admin/logout",
        headers={"Origin": "https://evil.example"},
        allow_redirects=False
    )
    if resp.status_code == 403:
        results.add_pass("POST with Origin: https://evil.example returns 403")
    else:
        results.add_fail("CSRF evil origin", f"Expected 403, got {resp.status_code}")
    
    # Test 3: Sec-Fetch-Site: same-site (should also be rejected, only same-origin allowed)
    print_info("Testing POST with Sec-Fetch-Site: same-site")
    resp = s1.post(
        f"{BASE_URL}/admin/logout",
        headers={"Sec-Fetch-Site": "same-site"},
        allow_redirects=False
    )
    if resp.status_code == 403:
        results.add_pass("POST with Sec-Fetch-Site: same-site returns 403")
    else:
        results.add_fail("CSRF same-site", f"Expected 403, got {resp.status_code}")
    
    # Verify S1 still works after failed CSRF attempts
    resp = s1.get(f"{BASE_URL}/api/admin/map.json")
    if resp.status_code == 200:
        results.add_pass("S1 still works after CSRF attempts (200)")
    else:
        results.add_fail("S1 after CSRF", f"Status {resp.status_code}")

def test_scenario_5_post_logout_same_origin(s1: requests.Session):
    """Scenario 5: POST /admin/logout with same-origin headers should work"""
    print_section("Scenario 5: POST /admin/logout with same-origin headers")
    
    # POST with same-origin headers
    resp = s1.post(
        f"{BASE_URL}/admin/logout",
        headers={
            "Origin": BASE_URL,
            "Sec-Fetch-Site": "same-origin"
        },
        allow_redirects=False
    )
    
    # Should return 303
    if resp.status_code == 303:
        results.add_pass("POST /admin/logout returns 303")
    else:
        results.add_fail("POST logout status", f"Expected 303, got {resp.status_code}")
    
    # Should redirect to /admin/access
    location = resp.headers.get('Location', '')
    if location == '/admin/access':
        results.add_pass("POST /admin/logout redirects to /admin/access")
    else:
        results.add_fail("POST logout redirect", f"Expected /admin/access, got {location}")
    
    # Should clear cookie (Set-Cookie with Max-Age=0 or expired)
    set_cookie = resp.headers.get('Set-Cookie', '')
    if 'cim_session' in set_cookie and ('Max-Age=0' in set_cookie or 'expires=' in set_cookie.lower()):
        results.add_pass("POST /admin/logout clears cookie (Max-Age=0 or expired)")
    else:
        results.add_fail("POST logout cookie", f"Cookie not cleared properly: {set_cookie}")
    
    # Verify cookie attributes
    if 'HttpOnly' in set_cookie:
        results.add_pass("Cookie has HttpOnly attribute")
    else:
        results.add_fail("Cookie HttpOnly", "Missing HttpOnly attribute")
    
    if 'SameSite=lax' in set_cookie or 'SameSite=Lax' in set_cookie:
        results.add_pass("Cookie has SameSite=lax attribute")
    else:
        results.add_fail("Cookie SameSite", f"Missing or wrong SameSite: {set_cookie}")
    
    if 'Path=/' in set_cookie:
        results.add_pass("Cookie has Path=/ attribute")
    else:
        results.add_fail("Cookie Path", "Missing Path=/ attribute")

def test_scenario_6_old_cookie_rejected(old_cookie: str):
    """Scenario 6: OLD_COOKIE should be rejected on all protected endpoints"""
    print_section("Scenario 6: OLD_COOKIE Rejected")
    
    # Create a new session with OLD_COOKIE
    s_old = requests.Session()
    s_old.cookies.set('cim_session', old_cookie, domain='localhost', path='/')
    
    # Test GET /admin/
    print_info("Testing GET /admin/ with OLD_COOKIE")
    resp = s_old.get(f"{BASE_URL}/admin/", allow_redirects=False)
    if resp.status_code in [303, 401, 403]:
        results.add_pass(f"GET /admin/ with OLD_COOKIE rejected ({resp.status_code})")
    else:
        results.add_fail("OLD_COOKIE /admin/", f"Expected 303/401/403, got {resp.status_code}")
    
    # Test GET /admin/system
    print_info("Testing GET /admin/system with OLD_COOKIE")
    resp = s_old.get(f"{BASE_URL}/admin/system", allow_redirects=False)
    if resp.status_code in [303, 401, 403]:
        results.add_pass(f"GET /admin/system with OLD_COOKIE rejected ({resp.status_code})")
    else:
        results.add_fail("OLD_COOKIE /admin/system", f"Expected 303/401/403, got {resp.status_code}")
    
    # Test GET /admin/cloud
    print_info("Testing GET /admin/cloud with OLD_COOKIE")
    resp = s_old.get(f"{BASE_URL}/admin/cloud", allow_redirects=False)
    if resp.status_code in [303, 401, 403]:
        results.add_pass(f"GET /admin/cloud with OLD_COOKIE rejected ({resp.status_code})")
    else:
        results.add_fail("OLD_COOKIE /admin/cloud", f"Expected 303/401/403, got {resp.status_code}")
    
    # Test GET /api/admin/map.json
    print_info("Testing GET /api/admin/map.json with OLD_COOKIE")
    resp = s_old.get(f"{BASE_URL}/api/admin/map.json")
    if resp.status_code in [401, 403]:
        results.add_pass(f"GET /api/admin/map.json with OLD_COOKIE rejected ({resp.status_code})")
    else:
        results.add_fail("OLD_COOKIE /api/admin/map.json", f"Expected 401/403, got {resp.status_code}")
    
    # Test POST /admin/system/test-db with same-origin headers
    print_info("Testing POST /admin/system/test-db with OLD_COOKIE")
    resp = s_old.post(
        f"{BASE_URL}/admin/system/test-db",
        headers={
            "Origin": BASE_URL,
            "Sec-Fetch-Site": "same-origin"
        },
        allow_redirects=False
    )
    if resp.status_code in [303, 401, 403]:
        results.add_pass(f"POST /admin/system/test-db with OLD_COOKIE rejected ({resp.status_code})")
    else:
        results.add_fail("OLD_COOKIE POST /admin/system/test-db", f"Expected 303/401/403, got {resp.status_code}")

def test_scenario_7_s2_still_works(s2: requests.Session):
    """Scenario 7: S2 should still work after S1 logout"""
    print_section("Scenario 7: S2 Still Works")
    
    resp = s2.get(f"{BASE_URL}/admin/", allow_redirects=False)
    if resp.status_code == 200:
        results.add_pass("S2 can still access /admin/ (200)")
    else:
        results.add_fail("S2 access /admin/", f"Status {resp.status_code}")
    
    resp = s2.get(f"{BASE_URL}/api/admin/map.json")
    if resp.status_code == 200:
        results.add_pass("S2 can still access /api/admin/map.json (200)")
    else:
        results.add_fail("S2 access /api/admin/map.json", f"Status {resp.status_code}")

def test_scenario_8_fresh_login():
    """Scenario 8: Fresh login after logout should work"""
    print_section("Scenario 8: Fresh Login After Logout")
    
    s_new = requests.Session()
    if login(s_new, TEST_USERNAME, TEST_PASSWORD):
        results.add_pass("Fresh login after logout successful")
        
        # Verify new session works
        resp = s_new.get(f"{BASE_URL}/admin/", allow_redirects=False)
        if resp.status_code == 200:
            results.add_pass("New session can access /admin/ (200)")
        else:
            results.add_fail("New session /admin/", f"Status {resp.status_code}")
        
        return s_new
    else:
        results.add_fail("Fresh login", "Login failed")
        return None

def test_scenario_9_logout_edge_cases(old_cookie: str):
    """Scenario 9: POST /admin/logout without cookie or with revoked cookie should not error"""
    print_section("Scenario 9: Logout Edge Cases")
    
    # Test 1: POST /admin/logout without any cookie
    print_info("Testing POST /admin/logout without cookie")
    s_no_cookie = requests.Session()
    resp = s_no_cookie.post(
        f"{BASE_URL}/admin/logout",
        headers={
            "Origin": BASE_URL,
            "Sec-Fetch-Site": "same-origin"
        },
        allow_redirects=False
    )
    if resp.status_code == 303:
        results.add_pass("POST /admin/logout without cookie returns 303 (no 500)")
    else:
        results.add_fail("Logout no cookie", f"Expected 303, got {resp.status_code}")
    
    # Test 2: POST /admin/logout with already revoked cookie
    print_info("Testing POST /admin/logout with already revoked cookie")
    s_revoked = requests.Session()
    s_revoked.cookies.set('cim_session', old_cookie, domain='localhost', path='/')
    resp = s_revoked.post(
        f"{BASE_URL}/admin/logout",
        headers={
            "Origin": BASE_URL,
            "Sec-Fetch-Site": "same-origin"
        },
        allow_redirects=False
    )
    if resp.status_code == 303:
        results.add_pass("POST /admin/logout with revoked cookie returns 303 (no 500)")
    else:
        results.add_fail("Logout revoked cookie", f"Expected 303, got {resp.status_code}")

def test_scenario_10_logout_buttons():
    """Scenario 10: Check that logout buttons render as POST forms"""
    print_section("Scenario 10: Logout Buttons as POST Forms")
    
    # Login to get access to pages
    s = requests.Session()
    if not login(s, TEST_USERNAME, TEST_PASSWORD):
        results.add_fail("Login for button test", "Failed to login")
        return
    
    # Test GET /admin/ HTML
    print_info("Checking GET /admin/ HTML for logout button")
    resp = s.get(f"{BASE_URL}/admin/")
    if resp.status_code == 200:
        html = resp.text
        
        # Check for POST form
        if '<form method="post" action="/admin/logout"' in html:
            results.add_pass("GET /admin/ contains POST form for logout")
        else:
            results.add_fail("Logout form method", "No POST form found in /admin/")
        
        # Check for data-testid
        if 'data-testid="logout-button"' in html:
            results.add_pass("GET /admin/ contains data-testid='logout-button'")
        else:
            results.add_fail("Logout button testid", "No data-testid='logout-button' found")
    else:
        results.add_fail("GET /admin/ for button test", f"Status {resp.status_code}")
    
    # Note: /admin/schimba-parola is only reachable when password change is required, so we skip it

def check_backend_logs():
    """Check backend logs for any 500 errors"""
    print_section("Backend Logs Check")
    
    try:
        import subprocess
        result = subprocess.run(
            ["tail", "-n", "100", "/var/log/supervisor/backend.err.log"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            log_content = result.stdout
            
            # Check for 500 errors
            if "500" in log_content or "Internal Server Error" in log_content:
                print_info("Found 500 errors in backend logs (showing last 20 lines):")
                lines = log_content.split('\n')
                for line in lines[-20:]:
                    if line.strip():
                        print(f"  {line}")
                results.add_fail("Backend logs", "500 errors found in logs")
            else:
                results.add_pass("No 500 errors in backend logs")
        else:
            print_info("Could not read backend logs")
    except Exception as e:
        print_info(f"Could not check backend logs: {e}")

def main():
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}Secure Logout Test - Cimitir FastAPI App{RESET}")
    print(f"{BLUE}Base URL: {BASE_URL}{RESET}")
    print(f"{BLUE}{'='*80}{RESET}")
    
    # Scenario 1: Two independent sessions
    s1, s2 = test_scenario_1_two_sessions()
    if not s1 or not s2:
        print(f"\n{RED}Cannot continue without two valid sessions{RESET}")
        return 1
    
    # Scenario 2: Capture OLD_COOKIE
    old_cookie = test_scenario_2_capture_cookie(s1)
    if not old_cookie:
        print(f"\n{RED}Cannot continue without OLD_COOKIE{RESET}")
        return 1
    
    # Scenario 3: GET /admin/logout (harmless)
    test_scenario_3_get_logout(s1)
    
    # Scenario 4: CSRF protection
    test_scenario_4_csrf_protection(s1)
    
    # Scenario 5: POST /admin/logout with same-origin headers
    test_scenario_5_post_logout_same_origin(s1)
    
    # Scenario 6: OLD_COOKIE rejected
    test_scenario_6_old_cookie_rejected(old_cookie)
    
    # Scenario 7: S2 still works
    test_scenario_7_s2_still_works(s2)
    
    # Scenario 8: Fresh login after logout
    s_new = test_scenario_8_fresh_login()
    
    # Scenario 9: Logout edge cases
    test_scenario_9_logout_edge_cases(old_cookie)
    
    # Scenario 10: Logout buttons as POST forms
    test_scenario_10_logout_buttons()
    
    # Check backend logs
    check_backend_logs()
    
    # Cleanup: logout all sessions
    print_section("Cleanup: Logout All Sessions")
    for s in [s2, s_new]:
        if s:
            try:
                s.post(
                    f"{BASE_URL}/admin/logout",
                    headers={
                        "Origin": BASE_URL,
                        "Sec-Fetch-Site": "same-origin"
                    }
                )
                print_info("Session logged out")
            except:
                pass
    
    # Print summary
    success = results.summary()
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
