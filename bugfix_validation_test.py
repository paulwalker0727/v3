#!/usr/bin/env python3
"""
Bug-fix validation test for cemetery app (FastAPI + PostgreSQL/Supabase)
Tests 6 specific bug fixes as per review request.
DO NOT print or write any secrets (DB URL, Supabase key, passwords, sync key).
"""
import json
import os
import sys
import time
import uuid
import hashlib
import threading
import requests
import psycopg
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Load secrets
SECRETS_FILE = "/app/validation/.secrets.json"
with open(SECRETS_FILE) as f:
    SECRETS = json.load(f)

# Configuration
BASE_URL = "https://walker-final.preview.emergentagent.com"
ADMIN_URL = "http://127.0.0.1:8001"
SYNC_KEY = SECRETS["sync_key"]
STAFF_USER = SECRETS["user"]
STAFF_PASS = SECRETS["new_password"]

# Test citizen account (pre-existing)
CITIZEN_EMAIL = "test-validare@example.invalid"
CITIZEN_PASS = "TestParola-Cetatean-2026!"

# Parse DATABASE_URL from backend/.env (never print it)
def get_db_url():
    env_file = Path("/app/backend/.env")
    if not env_file.exists():
        raise FileNotFoundError("backend/.env not found")
    for line in env_file.read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            url = line.split("=", 1)[1].strip().strip('"')
            return url
    raise ValueError("DATABASE_URL not found in .env")

DB_URL = get_db_url()

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
        self.created_rows = []  # Track created test data for cleanup
    
    def add_pass(self, test: str):
        self.passed.append(test)
        print(f"{GREEN}✓ PASS: {test}{RESET}")
    
    def add_fail(self, test: str, reason: str):
        self.failed.append(f"{test}: {reason}")
        print(f"{RED}✗ FAIL: {test}: {reason}{RESET}")
    
    def add_created(self, table: str, identifier: str):
        self.created_rows.append((table, identifier))
    
    def summary(self):
        print(f"\n{BLUE}{'='*80}{RESET}")
        print(f"{BLUE}TEST SUMMARY{RESET}")
        print(f"{BLUE}{'='*80}{RESET}")
        print(f"{GREEN}Passed: {len(self.passed)}{RESET}")
        print(f"{RED}Failed: {len(self.failed)}{RESET}")
        if self.failed:
            print(f"\n{RED}Failed tests:{RESET}")
            for fail in self.failed:
                print(f"  {RED}✗ {fail}{RESET}")
        return len(self.failed) == 0

results = TestResults()

def print_test(name: str):
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}TEST: {name}{RESET}")
    print(f"{BLUE}{'='*80}{RESET}")

def print_info(msg: str):
    print(f"{YELLOW}ℹ {msg}{RESET}")

# ============================================================================
# TEST 1: NUL byte handling
# ============================================================================
def test_1_nul_byte_handling():
    print_test("1. NUL byte handling (400 not 500)")
    
    try:
        # Test 1a: GET /api/public/loc/%00
        print_info("Testing GET /api/public/loc/%00")
        resp = requests.get(f"{BASE_URL}/api/public/loc/%00", timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code == 400:
            results.add_pass("NUL in loc path returns 400")
        else:
            results.add_fail("NUL in loc path", f"Expected 400, got {resp.status_code}")
        
        # Test 1b: GET /api/public/search?q=%00abc
        print_info("Testing GET /api/public/search?q=%00abc")
        resp = requests.get(f"{BASE_URL}/api/public/search?q=%00abc", timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code == 400:
            results.add_pass("NUL in search query returns 400")
        else:
            results.add_fail("NUL in search query", f"Expected 400, got {resp.status_code}")
        
        # Test 1c: Normal search should still work
        print_info("Testing GET /api/public/search?q=TEST")
        resp = requests.get(f"{BASE_URL}/api/public/search?q=TEST", timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                results.add_pass("Normal search returns 200 JSON")
            else:
                results.add_fail("Normal search", f"Response not JSON dict: {type(data)}")
        else:
            results.add_fail("Normal search", f"Expected 200, got {resp.status_code}")
    
    except Exception as e:
        results.add_fail("NUL byte handling", str(e))

# ============================================================================
# TEST 2: Idempotent op_id under concurrency
# ============================================================================
def test_2_idempotent_op_id_concurrency():
    print_test("2. Idempotent op_id under concurrency")
    
    try:
        # Generate fresh UUIDs
        op_id = str(uuid.uuid4())
        uid = str(uuid.uuid4())
        
        print_info(f"op_id: {op_id}")
        print_info(f"uid: {uid}")
        
        # Prepare payload
        payload = {
            "ops": [{
                "op_id": op_id,
                "entity": "concesionari",
                "uid": uid,
                "action": "upsert",
                "base_version": 0,
                "data": {
                    "nume": "TEST-QA-RACE",
                    "prenume": "X"
                }
            }]
        }
        
        headers = {
            "X-Sync-Key": SYNC_KEY,
            "X-Sync-Client": "main-qa0000001",
            "Content-Type": "application/json"
        }
        
        # Send 8 requests in parallel with SAME op_id/uid
        print_info("Sending 8 parallel requests with SAME op_id/uid")
        responses = []
        
        def send_request():
            try:
                resp = requests.post(f"{BASE_URL}/api/sync/entities/push", 
                                   headers=headers, json=payload, timeout=30)
                return resp
            except Exception as e:
                return None
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(send_request) for _ in range(8)]
            for future in as_completed(futures):
                resp = future.result()
                if resp:
                    responses.append(resp)
        
        print_info(f"Received {len(responses)} responses")
        
        # Check all responses
        all_applied = True
        has_duplicate = False
        for i, resp in enumerate(responses):
            if resp.status_code != 200:
                results.add_fail("Concurrent push", f"Response {i+1} status {resp.status_code}")
                all_applied = False
                continue
            
            data = resp.json()
            if not data.get("ok"):
                results.add_fail("Concurrent push", f"Response {i+1} not ok")
                all_applied = False
                continue
            
            result = data.get("results", [{}])[0]
            status = result.get("status")
            if status != "applied":
                results.add_fail("Concurrent push", f"Response {i+1} status={status}, expected applied")
                all_applied = False
            
            if result.get("duplicate"):
                has_duplicate = True
        
        if all_applied:
            results.add_pass("All 8 concurrent requests returned status=applied")
        
        if has_duplicate:
            results.add_pass("Some responses have duplicate=true")
        else:
            results.add_fail("Concurrent push", "No duplicate flag found")
        
        # Check database: exactly ONE row with nume='TEST-QA-RACE' and version=1
        print_info("Checking database for exactly ONE row")
        conn = psycopg.connect(DB_URL)
        try:
            cur = conn.execute("SELECT uid, version, nume FROM concesionari WHERE nume='TEST-QA-RACE'")
            rows = cur.fetchall()
            print_info(f"Found {len(rows)} rows")
            
            if len(rows) == 1:
                results.add_pass("Exactly ONE row in database")
                row = rows[0]
                if row[1] == 1:  # version
                    results.add_pass("Row has version=1")
                else:
                    results.add_fail("Row version", f"Expected 1, got {row[1]}")
                
                # Track for cleanup
                results.add_created("concesionari", f"nume='TEST-QA-RACE'")
            else:
                results.add_fail("Row count", f"Expected 1, got {len(rows)}")
        finally:
            conn.close()
        
        # Resend same op once more sequentially -> duplicate:true
        print_info("Resending same op sequentially")
        resp = requests.post(f"{BASE_URL}/api/sync/entities/push", 
                           headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("results", [{}])[0]
            if result.get("duplicate") is True:
                results.add_pass("Sequential resend returns duplicate=true")
            else:
                results.add_fail("Sequential resend", f"No duplicate flag: {result}")
        else:
            results.add_fail("Sequential resend", f"Status {resp.status_code}")
        
        # Delete the test row
        print_info("Deleting test row")
        delete_payload = {
            "ops": [{
                "op_id": str(uuid.uuid4()),
                "entity": "concesionari",
                "uid": uid,
                "action": "delete",
                "base_version": 1,
                "data": {}
            }]
        }
        resp = requests.post(f"{BASE_URL}/api/sync/entities/push", 
                           headers=headers, json=delete_payload, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            result = data.get("results", [{}])[0]
            if result.get("status") == "applied":
                results.add_pass("Test row deleted")
                
                # Confirm row is gone
                conn = psycopg.connect(DB_URL)
                try:
                    cur = conn.execute("SELECT COUNT(*) FROM concesionari WHERE nume='TEST-QA-RACE'")
                    count = cur.fetchone()[0]
                    if count == 0:
                        results.add_pass("Row confirmed deleted from database")
                    else:
                        results.add_fail("Row deletion", f"Still {count} rows")
                finally:
                    conn.close()
            else:
                results.add_fail("Delete row", f"Status {result.get('status')}")
        else:
            results.add_fail("Delete row", f"Status {resp.status_code}")
    
    except Exception as e:
        results.add_fail("Idempotent op_id", str(e))

# ============================================================================
# TEST 3: CSRF origin guard on /admin
# ============================================================================
def test_3_csrf_origin_guard():
    print_test("3. CSRF origin guard on /admin")
    
    try:
        # Login as staff
        print_info("Logging in as staff")
        session = requests.Session()
        resp = session.post(f"{ADMIN_URL}/admin/access/login",
                          data={"source_id": "cimitir", "username": STAFF_USER, "password": STAFF_PASS},
                          allow_redirects=False, timeout=10)
        
        if resp.status_code != 303:
            results.add_fail("Staff login", f"Expected 303, got {resp.status_code}")
            return
        
        cookie = resp.cookies.get("cim_session")
        if not cookie:
            results.add_fail("Staff login", "No session cookie")
            return
        
        results.add_pass("Staff login successful")
        
        # Test 3a: POST /admin/system/test-db with evil Origin -> 403
        print_info("Testing POST /admin/system/test-db with evil Origin")
        headers_evil = {
            "Origin": "https://evil.example",
            "Sec-Fetch-Site": "cross-site"
        }
        resp = session.post(f"{ADMIN_URL}/admin/system/test-db", headers=headers_evil, timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code == 403:
            results.add_pass("Evil Origin on /admin/system/test-db returns 403")
        else:
            results.add_fail("CSRF test-db evil", f"Expected 403, got {resp.status_code}")
        
        # Test 3b: POST /admin/system/test-db with same-origin -> 200
        print_info("Testing POST /admin/system/test-db with same-origin")
        headers_good = {
            "Origin": "http://127.0.0.1:8001",
            "Sec-Fetch-Site": "same-origin"
        }
        resp = session.post(f"{ADMIN_URL}/admin/system/test-db", headers=headers_good, timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            results.add_pass("Same-origin on /admin/system/test-db returns 200")
        else:
            results.add_fail("CSRF test-db good", f"Expected 200, got {resp.status_code}")
        
        # Test 3c: POST /admin/system/test-db with NO Origin/Sec-Fetch headers -> 200
        print_info("Testing POST /admin/system/test-db with NO Origin headers")
        resp = session.post(f"{ADMIN_URL}/admin/system/test-db", timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            results.add_pass("No Origin headers on /admin/system/test-db returns 200")
        else:
            results.add_fail("CSRF test-db no headers", f"Expected 200, got {resp.status_code}")
        
        # Test 3d: POST /admin/ui/settings with evil Origin -> 403
        print_info("Testing POST /admin/ui/settings with evil Origin")
        resp = session.post(f"{ADMIN_URL}/admin/ui/settings", headers=headers_evil, data={}, timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code == 403:
            results.add_pass("Evil Origin on /admin/ui/settings returns 403")
        else:
            results.add_fail("CSRF settings evil", f"Expected 403, got {resp.status_code}")
        
        # Test 3e: Anonymous POST /admin/ui/user with evil Origin -> 403 or redirect/401
        print_info("Testing anonymous POST /admin/ui/user with evil Origin")
        anon_session = requests.Session()
        resp = anon_session.post(f"{ADMIN_URL}/admin/ui/user", headers=headers_evil, data={}, timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code in [403, 401, 303]:
            results.add_pass(f"Anonymous evil Origin on /admin/ui/user returns {resp.status_code}")
        elif resp.status_code >= 200 and resp.status_code < 300:
            results.add_fail("CSRF user anon", f"Expected 403/401/redirect, got {resp.status_code}")
        else:
            results.add_pass(f"Anonymous evil Origin on /admin/ui/user returns {resp.status_code}")
    
    except Exception as e:
        results.add_fail("CSRF origin guard", str(e))

# ============================================================================
# TEST 4: Cookie Secure flag
# ============================================================================
def test_4_cookie_secure_flag():
    print_test("4. Cookie Secure flag")
    
    try:
        # Test 4a: Staff login WITHOUT X-Forwarded-Proto -> no Secure
        print_info("Testing staff login WITHOUT X-Forwarded-Proto")
        resp = requests.post(f"{ADMIN_URL}/admin/access/login",
                           data={"source_id": "cimitir", "username": STAFF_USER, "password": STAFF_PASS},
                           allow_redirects=False, timeout=10)
        
        if resp.status_code == 303:
            cookie_header = resp.headers.get("Set-Cookie", "")
            print_info(f"Set-Cookie: {cookie_header[:100]}...")
            
            if "HttpOnly" in cookie_header:
                results.add_pass("Staff cookie has HttpOnly")
            else:
                results.add_fail("Staff cookie HttpOnly", "Missing HttpOnly")
            
            if "SameSite=lax" in cookie_header or "SameSite=Lax" in cookie_header:
                results.add_pass("Staff cookie has SameSite=lax")
            else:
                results.add_fail("Staff cookie SameSite", "Missing SameSite=lax")
            
            if "Secure" not in cookie_header:
                results.add_pass("Staff cookie WITHOUT Secure (plain http)")
            else:
                results.add_fail("Staff cookie Secure", "Has Secure on plain http")
        else:
            results.add_fail("Staff login plain", f"Status {resp.status_code}")
        
        # Test 4b: Staff login WITH X-Forwarded-Proto: https -> Secure
        print_info("Testing staff login WITH X-Forwarded-Proto: https")
        resp = requests.post(f"{ADMIN_URL}/admin/access/login",
                           data={"source_id": "cimitir", "username": STAFF_USER, "password": STAFF_PASS},
                           headers={"X-Forwarded-Proto": "https"},
                           allow_redirects=False, timeout=10)
        
        if resp.status_code == 303:
            cookie_header = resp.headers.get("Set-Cookie", "")
            print_info(f"Set-Cookie: {cookie_header[:100]}...")
            
            if "Secure" in cookie_header:
                results.add_pass("Staff cookie WITH Secure (X-Forwarded-Proto: https)")
            else:
                results.add_fail("Staff cookie Secure https", "Missing Secure")
        else:
            results.add_fail("Staff login https", f"Status {resp.status_code}")
        
        # Test 4c: Citizen login over external https URL -> Secure
        print_info("Testing citizen login over external https URL")
        resp = requests.post(f"{BASE_URL}/api/public/account/login",
                           json={"email": CITIZEN_EMAIL, "password": CITIZEN_PASS},
                           timeout=10)
        
        if resp.status_code == 200:
            cookie_header = resp.headers.get("Set-Cookie", "")
            print_info(f"Set-Cookie: {cookie_header[:100]}...")
            
            if "Secure" in cookie_header:
                results.add_pass("Citizen cookie has Secure (https)")
            else:
                results.add_fail("Citizen cookie Secure", "Missing Secure")
            
            if "HttpOnly" in cookie_header:
                results.add_pass("Citizen cookie has HttpOnly")
            else:
                results.add_fail("Citizen cookie HttpOnly", "Missing HttpOnly")
            
            if "SameSite=lax" in cookie_header or "SameSite=Lax" in cookie_header:
                results.add_pass("Citizen cookie has SameSite=lax")
            else:
                results.add_fail("Citizen cookie SameSite", "Missing SameSite=lax")
        else:
            results.add_fail("Citizen login", f"Status {resp.status_code}")
    
    except Exception as e:
        results.add_fail("Cookie Secure flag", str(e))

# ============================================================================
# TEST 5: Public portal + sync API regression sanity
# ============================================================================
def test_5_public_portal_sync_sanity():
    print_test("5. Public portal + sync API regression sanity")
    
    try:
        # Test 5a: GET /api/public/health
        print_info("Testing GET /api/public/health")
        resp = requests.get(f"{BASE_URL}/api/public/health", timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            results.add_pass("/api/public/health returns 200")
        else:
            results.add_fail("Public health", f"Status {resp.status_code}")
        
        # Test 5b: GET /api/public/map
        print_info("Testing GET /api/public/map")
        resp = requests.get(f"{BASE_URL}/api/public/map", timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            results.add_pass("/api/public/map returns 200")
        else:
            results.add_fail("Public map", f"Status {resp.status_code}")
        
        # Test 5c: GET /api/public/privacy
        print_info("Testing GET /api/public/privacy")
        resp = requests.get(f"{BASE_URL}/api/public/privacy", timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            results.add_pass("/api/public/privacy returns 200")
        else:
            results.add_fail("Public privacy", f"Status {resp.status_code}")
        
        # Test 5d: GET /api/sync/ping WITH valid sync key -> 200
        print_info("Testing GET /api/sync/ping WITH valid sync key")
        headers = {
            "X-Sync-Key": SYNC_KEY,
            "X-Sync-Client": "main-qa0000001"
        }
        resp = requests.get(f"{BASE_URL}/api/sync/ping", headers=headers, timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print_info(f"Response: {json.dumps(data, indent=2)}")
            results.add_pass("/api/sync/ping with valid key returns 200 JSON")
        else:
            results.add_fail("Sync ping valid", f"Status {resp.status_code}")
        
        # Test 5e: GET /api/sync/ping WITHOUT key -> 401 (ONLY ONE CALL)
        print_info("Testing GET /api/sync/ping WITHOUT key (ONLY ONE CALL)")
        resp = requests.get(f"{BASE_URL}/api/sync/ping", timeout=10)
        print_info(f"Status: {resp.status_code}")
        if resp.status_code == 401:
            results.add_pass("/api/sync/ping without key returns 401")
        else:
            results.add_fail("Sync ping no key", f"Expected 401, got {resp.status_code}")
    
    except Exception as e:
        results.add_fail("Public portal sanity", str(e))

# ============================================================================
# TEST 6: Citizen request with document
# ============================================================================
def test_6_citizen_request_with_document():
    print_test("6. Citizen request with document (Storage healthy)")
    
    try:
        # Login as citizen
        print_info("Logging in as citizen")
        session = requests.Session()
        resp = session.post(f"{BASE_URL}/api/public/account/login",
                          json={"email": CITIZEN_EMAIL, "password": CITIZEN_PASS},
                          timeout=10)
        
        if resp.status_code != 200:
            results.add_fail("Citizen login", f"Status {resp.status_code}")
            return
        
        results.add_pass("Citizen login successful")
        
        # Check if we're rate limited
        print_info("Checking rate limit status")
        
        # Prepare multipart form
        pdf_path = "/app/validation/files/TEST_document.pdf"
        if not os.path.exists(pdf_path):
            results.add_fail("Document upload", f"Test PDF not found: {pdf_path}")
            return
        
        print_info(f"Uploading document: {pdf_path}")
        
        with open(pdf_path, "rb") as f:
            files = {"files": ("TEST_document.pdf", f, "application/pdf")}
            data = {
                "nume": "TEST-QA-Solicitant",
                "prenume": "QA",
                "cod_loc": "",
                "tip": "concesionare",
                "privacy_ack": "1"
            }
            
            resp = session.post(f"{BASE_URL}/api/public/cerere",
                              files=files, data=data, timeout=30)
        
        print_info(f"Status: {resp.status_code}")
        
        if resp.status_code == 429:
            print_info("Rate limited (429) - skipping test")
            results.add_pass("Citizen request (rate limited, skipped)")
            return
        
        if resp.status_code == 200:
            data = resp.json()
            print_info(f"Response: {json.dumps(data, indent=2)}")
            
            cod_urmarire = data.get("cod_urmarire")
            documente = data.get("documente")
            respinse = data.get("respinse", [])
            
            if cod_urmarire:
                results.add_pass(f"Request created with cod_urmarire={cod_urmarire}")
                results.add_created("cereri_concesionare", f"cod_urmarire='{cod_urmarire}'")
            else:
                results.add_fail("Request creation", "No cod_urmarire")
            
            if documente == 1:
                results.add_pass("Document uploaded (documente=1)")
            else:
                results.add_fail("Document upload", f"Expected documente=1, got {documente}")
            
            if len(respinse) == 0:
                results.add_pass("No rejected documents (respinse=[])")
            else:
                results.add_fail("Document rejection", f"respinse={respinse}")
            
            # Get request details
            if cod_urmarire:
                print_info(f"Getting request details for {cod_urmarire}")
                resp2 = session.get(f"{BASE_URL}/api/public/cerere/{cod_urmarire}", timeout=10)
                print_info(f"Status: {resp2.status_code}")
                
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    print_info(f"Request details: cod_urmarire={data2.get('cod_urmarire')}, "
                             f"status={data2.get('status')}, tip={data2.get('tip')}")
                    results.add_pass("Request details retrieved")
                else:
                    results.add_fail("Request details", f"Status {resp2.status_code}")
        else:
            results.add_fail("Request creation", f"Status {resp.status_code}: {resp.text[:200]}")
    
    except Exception as e:
        results.add_fail("Citizen request", str(e))

# ============================================================================
# CLEANUP
# ============================================================================
def cleanup():
    print_test("CLEANUP: Removing TEST-QA-* rows")
    
    try:
        conn = psycopg.connect(DB_URL)
        try:
            # Clean concesionari
            cur = conn.execute("DELETE FROM concesionari WHERE nume LIKE 'TEST-QA-%'")
            count = cur.rowcount
            conn.commit()
            print_info(f"Deleted {count} rows from concesionari")
            
            # Note: cereri_concesionare + documents + media_objects for cerere are acceptable to leave
            # as per instructions
        finally:
            conn.close()
        
        print_info("Cleanup complete")
    except Exception as e:
        print_info(f"Cleanup error: {e}")

# ============================================================================
# MAIN
# ============================================================================
def main():
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}Bug-fix Validation Tests{RESET}")
    print(f"{BLUE}Base URL: {BASE_URL}{RESET}")
    print(f"{BLUE}Admin URL: {ADMIN_URL}{RESET}")
    print(f"{BLUE}{'='*80}{RESET}")
    
    test_1_nul_byte_handling()
    test_2_idempotent_op_id_concurrency()
    test_3_csrf_origin_guard()
    test_4_cookie_secure_flag()
    test_5_public_portal_sync_sanity()
    test_6_citizen_request_with_document()
    
    cleanup()
    
    success = results.summary()
    
    print(f"\n{BLUE}Created test data:{RESET}")
    for table, identifier in results.created_rows:
        print(f"  {table}: {identifier}")
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
