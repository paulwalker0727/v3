#!/usr/bin/env python3
"""Test Cimitir Phase-3 Features - Contract Creation, PDF Documents, History/Audit
Tests all scenarios from the review request on http://localhost:8001
"""
import io
import json
import re
import requests
from typing import Dict, Any, List, Optional

# Configuration
BASE_URL = "http://localhost:8001"

# Test credentials (password for all: Tst-Mobil-4821x)
CREDENTIALS = {
    "tst_admin": {"username": "tst_admin", "password": "Tst-Mobil-4821x", "role": "admin"},
    "tst_op": {"username": "tst_op", "password": "Tst-Mobil-4821x", "role": "operator"},
    "tst_view": {"username": "tst_view", "password": "Tst-Mobil-4821x", "role": "view_only"}
}

# Test data
TEST_PLACES = {
    "TEST-MOB-1": {"contract_id": 1, "contract_nr": "TEST-C-1", "inhumat_id": 1, "plata_id": 1, "concesionar_id": 1},
    "TEST-MOB-2": {"contract_id": 2},
    "TEST-MOB-3": {"contract_id": 3},
    "TEST-MOB-4": {"free": True},  # Free, no contract
    "TEST-MOB-5": {"free": True}   # Free, no contract
}

# Color codes for output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

def print_test(name: str):
    """Print test name"""
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}TEST: {name}{RESET}")
    print(f"{BLUE}{'='*80}{RESET}")

def print_pass(msg: str):
    """Print success message"""
    print(f"{GREEN}✓ PASS: {msg}{RESET}")

def print_fail(msg: str):
    """Print failure message"""
    print(f"{RED}✗ FAIL: {msg}{RESET}")

def print_info(msg: str):
    """Print info message"""
    print(f"{YELLOW}ℹ INFO: {msg}{RESET}")

class TestResults:
    def __init__(self):
        self.passed = []
        self.failed = []
        self.sessions = {}  # Store session cookies
        self.created_contracts = []  # Store created contract IDs
    
    def add_pass(self, test: str):
        self.passed.append(test)
    
    def add_fail(self, test: str, reason: str):
        self.failed.append(f"{test}: {reason}")
    
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

def login(username: str, password: str) -> Optional[str]:
    """Login and return session cookie"""
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/access/login",
            data={"source_id": "cimitir", "username": username, "password": password},
            allow_redirects=False,
            timeout=10
        )
        if resp.status_code == 303:
            cookie = resp.cookies.get("cim_session")
            if cookie:
                print_info(f"Logged in as {username}, cookie: {cookie[:20]}...")
                return cookie
        print_fail(f"Login failed for {username}: status={resp.status_code}")
        return None
    except Exception as e:
        print_fail(f"Login exception for {username}: {e}")
        return None

def same_origin_headers() -> Dict[str, str]:
    """Headers for same-origin POST requests (CSRF protection)"""
    return {
        "Origin": BASE_URL,
        "Sec-Fetch-Site": "same-origin"
    }

def cross_origin_headers() -> Dict[str, str]:
    """Headers for cross-origin POST requests (should be rejected)"""
    return {
        "Origin": "https://evil.example",
        "Sec-Fetch-Site": "cross-site"
    }

def create_minimal_pdf() -> bytes:
    """Create minimal valid PDF"""
    return b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\nxref\n0 3\n0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\ntrailer<</Size 3/Root 1 0 R>>\nstartxref\n110\n%%EOF'

def test_1_login_all_users():
    """Test 1: Login all test users"""
    print_test("1. Login All Test Users")
    
    for username, creds in CREDENTIALS.items():
        cookie = login(creds["username"], creds["password"])
        if cookie:
            results.sessions[username] = cookie
            results.add_pass(f"Login {username}")
        else:
            results.add_fail(f"Login {username}", "Failed to get session cookie")

# ============================================================================
# (A) CONTRACT CREATION FROM WEB
# ============================================================================

def test_2a_contract_creation_permissions():
    """Test 2A: Contract creation permissions"""
    print_test("2A. Contract Creation - Permissions")
    
    cookie_view = results.sessions.get("tst_view")
    if not cookie_view:
        print_fail("No session for tst_view")
        results.add_fail("Contract creation - tst_view 403", "No session")
        return
    
    # Test 1: tst_view cannot create contract (403)
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-4/contract",
            data={"numar_contract": "TEST-VIEW-1", "concesionar_id": "1"},
            cookies={"cim_session": cookie_view},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 403:
            print_pass("tst_view contract creation returns 403")
            results.add_pass("Contract creation - tst_view 403")
        else:
            print_fail(f"tst_view should return 403: status={resp.status_code}")
            results.add_fail("Contract creation - tst_view 403", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"tst_view contract creation test: {e}")
        results.add_fail("Contract creation - tst_view 403", str(e))
    
    # Test 2: Anonymous returns 401/403
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-4/contract",
            data={"numar_contract": "TEST-ANON-1", "concesionar_id": "1"},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code in [401, 403]:
            print_pass(f"Anonymous contract creation returns {resp.status_code}")
            results.add_pass("Contract creation - anonymous blocked")
        else:
            print_fail(f"Anonymous should return 401/403: status={resp.status_code}")
            results.add_fail("Contract creation - anonymous blocked", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Anonymous contract creation test: {e}")
        results.add_fail("Contract creation - anonymous blocked", str(e))
    
    # Test 3: Cross-site headers return 403
    cookie_op = results.sessions.get("tst_op")
    if cookie_op:
        try:
            resp = requests.post(
                f"{BASE_URL}/admin/ui/loc/TEST-MOB-4/contract",
                data={"numar_contract": "TEST-CSRF-1", "concesionar_id": "1"},
                cookies={"cim_session": cookie_op},
                headers=cross_origin_headers(),
                timeout=10
            )
            if resp.status_code == 403:
                print_pass("Cross-site contract creation returns 403")
                results.add_pass("Contract creation - CSRF protection")
            else:
                print_fail(f"Cross-site should return 403: status={resp.status_code}")
                results.add_fail("Contract creation - CSRF protection", f"Status {resp.status_code}")
        except Exception as e:
            print_fail(f"CSRF contract creation test: {e}")
            results.add_fail("Contract creation - CSRF protection", str(e))

def test_2b_contract_creation_validation():
    """Test 2B: Contract creation validation"""
    print_test("2B. Contract Creation - Validation")
    
    cookie_op = results.sessions.get("tst_op")
    if not cookie_op:
        print_fail("No session for tst_op")
        results.add_fail("Contract creation validation", "No session")
        return
    
    # Test 1: Unknown place returns 404
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/NONEXISTENT-999/contract",
            data={"numar_contract": "TEST-404", "concesionar_id": "1"},
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 404:
            print_pass("Unknown place returns 404")
            results.add_pass("Contract validation - unknown place 404")
        else:
            print_fail(f"Unknown place should return 404: status={resp.status_code}")
            results.add_fail("Contract validation - unknown place 404", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Unknown place test: {e}")
        results.add_fail("Contract validation - unknown place 404", str(e))
    
    # Test 2: Empty numar_contract returns 422
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-4/contract",
            data={"numar_contract": "", "concesionar_id": "1"},
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 422 and "drawer-error" in resp.text:
            print_pass("Empty numar_contract returns 422 with drawer-error")
            results.add_pass("Contract validation - empty number")
        else:
            print_fail(f"Empty number should return 422: status={resp.status_code}")
            results.add_fail("Contract validation - empty number", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Empty number test: {e}")
        results.add_fail("Contract validation - empty number", str(e))
    
    # Test 3: Neither concesionar_id nor nume returns 422
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-4/contract",
            data={"numar_contract": "TEST-NO-CONC"},
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 422 and "drawer-error" in resp.text:
            print_pass("No concesionar returns 422 with drawer-error")
            results.add_pass("Contract validation - no concesionar")
        else:
            print_fail(f"No concesionar should return 422: status={resp.status_code}")
            results.add_fail("Contract validation - no concesionar", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"No concesionar test: {e}")
        results.add_fail("Contract validation - no concesionar", str(e))
    
    # Test 4: Nonexistent concesionar_id returns 422
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-4/contract",
            data={"numar_contract": "TEST-BAD-CONC", "concesionar_id": "99999"},
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 422 and "drawer-error" in resp.text:
            print_pass("Nonexistent concesionar_id returns 422 with drawer-error")
            results.add_pass("Contract validation - bad concesionar_id")
        else:
            print_fail(f"Bad concesionar_id should return 422: status={resp.status_code}")
            results.add_fail("Contract validation - bad concesionar_id", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Bad concesionar_id test: {e}")
        results.add_fail("Contract validation - bad concesionar_id", str(e))
    
    # Test 5: Invalid date returns 422
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-4/contract",
            data={
                "numar_contract": "TEST-BAD-DATE",
                "concesionar_id": "1",
                "data_contract": "31.31.2050"
            },
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 422 and "drawer-error" in resp.text:
            print_pass("Invalid date returns 422 with drawer-error")
            results.add_pass("Contract validation - invalid date")
        else:
            print_fail(f"Invalid date should return 422: status={resp.status_code}")
            results.add_fail("Contract validation - invalid date", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Invalid date test: {e}")
        results.add_fail("Contract validation - invalid date", str(e))
    
    # Test 6: End date before start returns 422
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-4/contract",
            data={
                "numar_contract": "TEST-BAD-RANGE",
                "concesionar_id": "1",
                "data_inceput": "01.01.2025",
                "data_sfarsit": "01.01.2024"
            },
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 422 and "drawer-error" in resp.text:
            print_pass("End before start returns 422 with drawer-error")
            results.add_pass("Contract validation - end before start")
        else:
            print_fail(f"End before start should return 422: status={resp.status_code}")
            results.add_fail("Contract validation - end before start", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"End before start test: {e}")
        results.add_fail("Contract validation - end before start", str(e))
    
    # Test 7: Duplicate contract number returns 422
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-4/contract",
            data={"numar_contract": "TEST-C-1", "concesionar_id": "1"},  # Duplicate of TEST-MOB-1
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 422 and "drawer-error" in resp.text:
            print_pass("Duplicate contract number returns 422 with drawer-error")
            results.add_pass("Contract validation - duplicate number")
        else:
            print_fail(f"Duplicate number should return 422: status={resp.status_code}")
            results.add_fail("Contract validation - duplicate number", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Duplicate number test: {e}")
        results.add_fail("Contract validation - duplicate number", str(e))
    
    # Test 8: Place with active contract returns 422
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-1/contract",
            data={"numar_contract": "TEST-OCCUPIED", "concesionar_id": "1"},
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 422 and "drawer-error" in resp.text:
            print_pass("Place with active contract returns 422 with drawer-error")
            results.add_pass("Contract validation - place occupied")
        else:
            print_fail(f"Occupied place should return 422: status={resp.status_code}")
            results.add_fail("Contract validation - place occupied", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Occupied place test: {e}")
        results.add_fail("Contract validation - place occupied", str(e))

def test_2c_contract_creation_success():
    """Test 2C: Contract creation success scenarios"""
    print_test("2C. Contract Creation - Success")
    
    cookie_op = results.sessions.get("tst_op")
    if not cookie_op:
        print_fail("No session for tst_op")
        results.add_fail("Contract creation success", "No session")
        return
    
    # Test 1: Create contract on TEST-MOB-4 with NEW concesionar
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-4/contract",
            data={
                "numar_contract": "TEST-NEW-C-4",
                "data_contract": "01.01.2025",
                "nume": "TestNume",
                "prenume": "TestPrenume",
                "cnp": "1234567890123",
                "telefon": "0712345678",
                "adresa": "Test Address",
                "bi_ci": "AB123456",
                "email": "test@example.com",
                "observatii": "Test contract"
            },
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 200:
            # Check for contract number in response
            if "TEST-NEW-C-4" in resp.text:
                print_pass("Contract created on TEST-MOB-4 with new concesionar")
                results.add_pass("Contract creation - TEST-MOB-4 new concesionar")
                
                # Extract contract ID from pdf-contract-<id> link
                match = re.search(r'pdf-contract-(\d+)', resp.text)
                if match:
                    contract_id = match.group(1)
                    results.created_contracts.append(contract_id)
                    print_info(f"Created contract ID: {contract_id}")
                    
                    # Check for PDF link
                    if f"/admin/pdf/contract/{contract_id}" in resp.text:
                        print_pass(f"PDF link present: /admin/pdf/contract/{contract_id}")
                        results.add_pass("Contract creation - PDF link present")
                    else:
                        print_fail("PDF link not found in response")
                        results.add_fail("Contract creation - PDF link", "Not found")
                else:
                    print_fail("Could not extract contract ID from response")
                    results.add_fail("Contract creation - extract ID", "Not found")
            else:
                print_fail("Contract number not found in response")
                results.add_fail("Contract creation - TEST-MOB-4 new concesionar", "Number not in response")
        else:
            print_fail(f"Contract creation failed: status={resp.status_code}, body={resp.text[:500]}")
            results.add_fail("Contract creation - TEST-MOB-4 new concesionar", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Contract creation TEST-MOB-4 test: {e}")
        results.add_fail("Contract creation - TEST-MOB-4 new concesionar", str(e))
    
    # Test 2: Create contract on TEST-MOB-5 with existing concesionar_id=1
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-5/contract",
            data={
                "numar_contract": "TEST-NEW-C-5",
                "data_contract": "01.01.2025",
                "concesionar_id": "1"
            },
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 200:
            if "TEST-NEW-C-5" in resp.text:
                print_pass("Contract created on TEST-MOB-5 with existing concesionar")
                results.add_pass("Contract creation - TEST-MOB-5 existing concesionar")
                
                # Extract contract ID
                match = re.search(r'pdf-contract-(\d+)', resp.text)
                if match:
                    contract_id = match.group(1)
                    results.created_contracts.append(contract_id)
                    print_info(f"Created contract ID: {contract_id}")
            else:
                print_fail("Contract number not found in response")
                results.add_fail("Contract creation - TEST-MOB-5 existing concesionar", "Number not in response")
        else:
            print_fail(f"Contract creation failed: status={resp.status_code}, body={resp.text[:500]}")
            results.add_fail("Contract creation - TEST-MOB-5 existing concesionar", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Contract creation TEST-MOB-5 test: {e}")
        results.add_fail("Contract creation - TEST-MOB-5 existing concesionar", str(e))

def test_2d_contract_creation_verification():
    """Test 2D: Verify created contracts appear in lists"""
    print_test("2D. Contract Creation - Verification")
    
    cookie_op = results.sessions.get("tst_op")
    if not cookie_op:
        print_fail("No session for tst_op")
        results.add_fail("Contract verification", "No session")
        return
    
    # Test 1: GET /admin/contracte lists both new contracts
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/contracte",
            cookies={"cim_session": cookie_op},
            timeout=10
        )
        if resp.status_code == 200:
            has_c4 = "TEST-NEW-C-4" in resp.text
            has_c5 = "TEST-NEW-C-5" in resp.text
            
            if has_c4 and has_c5:
                print_pass("Both new contracts appear in /admin/contracte")
                results.add_pass("Contract verification - list")
            else:
                print_fail(f"Contracts not in list: C4={has_c4}, C5={has_c5}")
                results.add_fail("Contract verification - list", f"C4={has_c4}, C5={has_c5}")
        else:
            print_fail(f"GET /admin/contracte failed: status={resp.status_code}")
            results.add_fail("Contract verification - list", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Contract list test: {e}")
        results.add_fail("Contract verification - list", str(e))
    
    # Test 2: GET /admin/ui/loc/TEST-MOB-4 shows the contract
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-4",
            cookies={"cim_session": cookie_op},
            timeout=10
        )
        if resp.status_code == 200:
            if "TEST-NEW-C-4" in resp.text:
                print_pass("Contract appears in TEST-MOB-4 drawer")
                results.add_pass("Contract verification - drawer TEST-MOB-4")
            else:
                print_fail("Contract not in TEST-MOB-4 drawer")
                results.add_fail("Contract verification - drawer TEST-MOB-4", "Not found")
        else:
            print_fail(f"GET drawer failed: status={resp.status_code}")
            results.add_fail("Contract verification - drawer TEST-MOB-4", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Drawer TEST-MOB-4 test: {e}")
        results.add_fail("Contract verification - drawer TEST-MOB-4", str(e))
    
    # Test 3: Second creation attempt on TEST-MOB-4 returns 422
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-4/contract",
            data={"numar_contract": "TEST-DUPLICATE", "concesionar_id": "1"},
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 422 and "drawer-error" in resp.text:
            print_pass("Second creation on TEST-MOB-4 returns 422")
            results.add_pass("Contract verification - duplicate creation")
        else:
            print_fail(f"Second creation should return 422: status={resp.status_code}")
            results.add_fail("Contract verification - duplicate creation", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Duplicate creation test: {e}")
        results.add_fail("Contract verification - duplicate creation", str(e))

def test_2e_contract_creation_ui():
    """Test 2E: Contract creation UI elements"""
    print_test("2E. Contract Creation - UI Elements")
    
    cookie_view = results.sessions.get("tst_view")
    cookie_op = results.sessions.get("tst_op")
    
    if not all([cookie_view, cookie_op]):
        print_fail("Missing sessions")
        results.add_fail("Contract UI", "Missing sessions")
        return
    
    # Test 1: tst_view drawer contains contract-no-right, NOT contract-add-toggle
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-4",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200:
            has_no_right = "contract-no-right" in resp.text
            has_add_toggle = "contract-add-toggle" in resp.text
            
            if has_no_right and not has_add_toggle:
                print_pass("tst_view drawer has contract-no-right, no contract-add-toggle")
                results.add_pass("Contract UI - tst_view no rights")
            else:
                print_fail(f"tst_view UI wrong: no_right={has_no_right}, add_toggle={has_add_toggle}")
                results.add_fail("Contract UI - tst_view no rights", f"no_right={has_no_right}, add={has_add_toggle}")
        else:
            print_fail(f"GET drawer failed: status={resp.status_code}")
            results.add_fail("Contract UI - tst_view no rights", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"tst_view UI test: {e}")
        results.add_fail("Contract UI - tst_view no rights", str(e))
    
    # Test 2: tst_op drawer contains contract-add-toggle
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-5",
            cookies={"cim_session": cookie_op},
            timeout=10
        )
        if resp.status_code == 200:
            if "contract-add-toggle" in resp.text:
                print_pass("tst_op drawer has contract-add-toggle")
                results.add_pass("Contract UI - tst_op has rights")
            else:
                print_fail("tst_op drawer missing contract-add-toggle")
                results.add_fail("Contract UI - tst_op has rights", "Missing toggle")
        else:
            print_fail(f"GET drawer failed: status={resp.status_code}")
            results.add_fail("Contract UI - tst_op has rights", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"tst_op UI test: {e}")
        results.add_fail("Contract UI - tst_op has rights", str(e))

# ============================================================================
# (B) PDF DOCUMENTS
# ============================================================================

def test_3a_pdf_contract():
    """Test 3A: PDF contract documents"""
    print_test("3A. PDF Documents - Contracts")
    
    cookie_view = results.sessions.get("tst_view")
    if not cookie_view:
        print_fail("No session for tst_view")
        results.add_fail("PDF contract", "No session")
        return
    
    # Test existing contract ID 1
    contract_ids = ["1"] + results.created_contracts
    
    for contract_id in contract_ids:
        try:
            resp = requests.get(
                f"{BASE_URL}/admin/pdf/contract/{contract_id}",
                cookies={"cim_session": cookie_view},
                timeout=10
            )
            if resp.status_code == 200:
                # Check PDF magic bytes
                if resp.content.startswith(b'%PDF-'):
                    print_pass(f"PDF contract {contract_id} valid (starts with %PDF-)")
                    results.add_pass(f"PDF contract {contract_id}")
                    
                    # Check headers
                    if resp.headers.get("Content-Type") == "application/pdf":
                        print_pass(f"PDF contract {contract_id} has correct Content-Type")
                        results.add_pass(f"PDF contract {contract_id} Content-Type")
                    else:
                        print_fail(f"PDF contract {contract_id} wrong Content-Type: {resp.headers.get('Content-Type')}")
                        results.add_fail(f"PDF contract {contract_id} Content-Type", "Wrong type")
                    
                    if "inline" in resp.headers.get("Content-Disposition", ""):
                        print_pass(f"PDF contract {contract_id} has inline disposition")
                        results.add_pass(f"PDF contract {contract_id} inline")
                    else:
                        print_fail(f"PDF contract {contract_id} missing inline disposition")
                        results.add_fail(f"PDF contract {contract_id} inline", "Missing")
                    
                    if "private" in resp.headers.get("Cache-Control", "") or "no-store" in resp.headers.get("Cache-Control", ""):
                        print_pass(f"PDF contract {contract_id} has private/no-store Cache-Control")
                        results.add_pass(f"PDF contract {contract_id} cache")
                    else:
                        print_fail(f"PDF contract {contract_id} wrong Cache-Control: {resp.headers.get('Cache-Control')}")
                        results.add_fail(f"PDF contract {contract_id} cache", "Wrong cache")
                else:
                    print_fail(f"PDF contract {contract_id} doesn't start with %PDF-")
                    results.add_fail(f"PDF contract {contract_id}", "Invalid PDF")
            else:
                print_fail(f"PDF contract {contract_id} failed: status={resp.status_code}")
                results.add_fail(f"PDF contract {contract_id}", f"Status {resp.status_code}")
        except Exception as e:
            print_fail(f"PDF contract {contract_id} test: {e}")
            results.add_fail(f"PDF contract {contract_id}", str(e))
    
    # Test download=1 parameter
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/pdf/contract/1?download=1",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200:
            if "attachment" in resp.headers.get("Content-Disposition", ""):
                print_pass("PDF contract with ?download=1 has attachment disposition")
                results.add_pass("PDF contract download=1")
            else:
                print_fail("PDF contract ?download=1 missing attachment disposition")
                results.add_fail("PDF contract download=1", "Missing attachment")
        else:
            print_fail(f"PDF contract ?download=1 failed: status={resp.status_code}")
            results.add_fail("PDF contract download=1", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF contract download=1 test: {e}")
        results.add_fail("PDF contract download=1", str(e))
    
    # Test nonexistent contract ID
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/pdf/contract/99999",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 404:
            print_pass("PDF contract nonexistent ID returns 404")
            results.add_pass("PDF contract 404")
        else:
            print_fail(f"Nonexistent contract should return 404: status={resp.status_code}")
            results.add_fail("PDF contract 404", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF contract 404 test: {e}")
        results.add_fail("PDF contract 404", str(e))

def test_3b_pdf_other_documents():
    """Test 3B: PDF other documents (chitanta, adeverinta, etc.)"""
    print_test("3B. PDF Documents - Other Types")
    
    cookie_view = results.sessions.get("tst_view")
    if not cookie_view:
        print_fail("No session for tst_view")
        results.add_fail("PDF other docs", "No session")
        return
    
    # Test chitanta (plata_id=1)
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/pdf/chitanta/1",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200 and resp.content.startswith(b'%PDF-'):
            print_pass("PDF chitanta/1 valid")
            results.add_pass("PDF chitanta")
        else:
            print_fail(f"PDF chitanta failed: status={resp.status_code}")
            results.add_fail("PDF chitanta", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF chitanta test: {e}")
        results.add_fail("PDF chitanta", str(e))
    
    # Test adeverinta (inhumat_id=1)
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/pdf/adeverinta/1",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200 and resp.content.startswith(b'%PDF-'):
            print_pass("PDF adeverinta/1 valid")
            results.add_pass("PDF adeverinta")
        else:
            print_fail(f"PDF adeverinta failed: status={resp.status_code}")
            results.add_fail("PDF adeverinta", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF adeverinta test: {e}")
        results.add_fail("PDF adeverinta", str(e))
    
    # Test expirari with days=30
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/pdf/expirari?days=30",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200 and resp.content.startswith(b'%PDF-'):
            print_pass("PDF expirari days=30 valid")
            results.add_pass("PDF expirari days=30")
        else:
            print_fail(f"PDF expirari days=30 failed: status={resp.status_code}")
            results.add_fail("PDF expirari days=30", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF expirari days=30 test: {e}")
        results.add_fail("PDF expirari days=30", str(e))
    
    # Test expirari with days=90
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/pdf/expirari?days=90",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200 and resp.content.startswith(b'%PDF-'):
            print_pass("PDF expirari days=90 valid")
            results.add_pass("PDF expirari days=90")
        else:
            print_fail(f"PDF expirari days=90 failed: status={resp.status_code}")
            results.add_fail("PDF expirari days=90", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF expirari days=90 test: {e}")
        results.add_fail("PDF expirari days=90", str(e))
    
    # Test raport-financiar without params
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/pdf/raport-financiar",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200 and resp.content.startswith(b'%PDF-'):
            print_pass("PDF raport-financiar (no params) valid")
            results.add_pass("PDF raport-financiar no params")
        else:
            print_fail(f"PDF raport-financiar failed: status={resp.status_code}")
            results.add_fail("PDF raport-financiar no params", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF raport-financiar test: {e}")
        results.add_fail("PDF raport-financiar no params", str(e))
    
    # Test raport-financiar with start/end
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/pdf/raport-financiar?start=2024-01-01&end=2024-12-31",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200 and resp.content.startswith(b'%PDF-'):
            print_pass("PDF raport-financiar (with dates) valid")
            results.add_pass("PDF raport-financiar with dates")
        else:
            print_fail(f"PDF raport-financiar with dates failed: status={resp.status_code}")
            results.add_fail("PDF raport-financiar with dates", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF raport-financiar with dates test: {e}")
        results.add_fail("PDF raport-financiar with dates", str(e))
    
    # Test registru-inhumari without params
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/pdf/registru-inhumari",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200 and resp.content.startswith(b'%PDF-'):
            print_pass("PDF registru-inhumari (no params) valid")
            results.add_pass("PDF registru-inhumari no params")
        else:
            print_fail(f"PDF registru-inhumari failed: status={resp.status_code}")
            results.add_fail("PDF registru-inhumari no params", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF registru-inhumari test: {e}")
        results.add_fail("PDF registru-inhumari no params", str(e))
    
    # Test registru-inhumari with start/end
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/pdf/registru-inhumari?start=2024-01-01&end=2024-12-31",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200 and resp.content.startswith(b'%PDF-'):
            print_pass("PDF registru-inhumari (with dates) valid")
            results.add_pass("PDF registru-inhumari with dates")
        else:
            print_fail(f"PDF registru-inhumari with dates failed: status={resp.status_code}")
            results.add_fail("PDF registru-inhumari with dates", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF registru-inhumari with dates test: {e}")
        results.add_fail("PDF registru-inhumari with dates", str(e))
    
    # Test fisa.pdf for TEST-MOB-1
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/loc/TEST-MOB-1/fisa.pdf",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200 and resp.content.startswith(b'%PDF-'):
            print_pass("PDF fisa TEST-MOB-1 valid")
            results.add_pass("PDF fisa")
        else:
            print_fail(f"PDF fisa failed: status={resp.status_code}")
            results.add_fail("PDF fisa", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF fisa test: {e}")
        results.add_fail("PDF fisa", str(e))

def test_3c_pdf_validation():
    """Test 3C: PDF validation (invalid params, auth)"""
    print_test("3C. PDF Documents - Validation")
    
    cookie_view = results.sessions.get("tst_view")
    if not cookie_view:
        print_fail("No session for tst_view")
        results.add_fail("PDF validation", "No session")
        return
    
    # Test invalid date param
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/pdf/raport-financiar?start=abc",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 422:
            print_pass("PDF with invalid date param returns 422")
            results.add_pass("PDF validation - invalid date")
        else:
            print_fail(f"Invalid date should return 422: status={resp.status_code}")
            results.add_fail("PDF validation - invalid date", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF invalid date test: {e}")
        results.add_fail("PDF validation - invalid date", str(e))
    
    # Test anonymous access
    try:
        resp = requests.get(f"{BASE_URL}/admin/pdf/contract/1", timeout=10)
        if resp.status_code in [401, 403]:
            print_pass(f"Anonymous PDF access returns {resp.status_code}")
            results.add_pass("PDF validation - anonymous blocked")
        else:
            print_fail(f"Anonymous should return 401/403: status={resp.status_code}")
            results.add_fail("PDF validation - anonymous blocked", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF anonymous test: {e}")
        results.add_fail("PDF validation - anonymous blocked", str(e))

def test_3d_pdf_ui_elements():
    """Test 3D: PDF UI elements"""
    print_test("3D. PDF Documents - UI Elements")
    
    cookie_view = results.sessions.get("tst_view")
    if not cookie_view:
        print_fail("No session for tst_view")
        results.add_fail("PDF UI", "No session")
        return
    
    # Test 1: GET /admin/rapoarte contains pdf-reports-card
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/rapoarte",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200:
            if "pdf-reports-card" in resp.text:
                print_pass("GET /admin/rapoarte has pdf-reports-card")
                results.add_pass("PDF UI - rapoarte card")
            else:
                print_fail("GET /admin/rapoarte missing pdf-reports-card")
                results.add_fail("PDF UI - rapoarte card", "Missing")
        else:
            print_fail(f"GET /admin/rapoarte failed: status={resp.status_code}")
            results.add_fail("PDF UI - rapoarte card", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF rapoarte UI test: {e}")
        results.add_fail("PDF UI - rapoarte card", str(e))
    
    # Test 2: Drawer for TEST-MOB-1 contains pdf-contract-1, pdf-chitanta-1, pdf-adeverinta-1
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/ui/loc/TEST-MOB-1",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200:
            has_contract = "pdf-contract-1" in resp.text
            has_chitanta = "pdf-chitanta-1" in resp.text
            has_adeverinta = "pdf-adeverinta-1" in resp.text
            
            if has_contract and has_chitanta and has_adeverinta:
                print_pass("TEST-MOB-1 drawer has all PDF links")
                results.add_pass("PDF UI - drawer links")
            else:
                print_fail(f"TEST-MOB-1 drawer missing PDF links: contract={has_contract}, chitanta={has_chitanta}, adeverinta={has_adeverinta}")
                results.add_fail("PDF UI - drawer links", f"contract={has_contract}, chitanta={has_chitanta}, adeverinta={has_adeverinta}")
        else:
            print_fail(f"GET drawer failed: status={resp.status_code}")
            results.add_fail("PDF UI - drawer links", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"PDF drawer UI test: {e}")
        results.add_fail("PDF UI - drawer links", str(e))

# ============================================================================
# (C) HISTORY/AUDIT
# ============================================================================

def test_4a_history_permissions():
    """Test 4A: History/audit permissions"""
    print_test("4A. History/Audit - Permissions")
    
    cookie_view = results.sessions.get("tst_view")
    cookie_op = results.sessions.get("tst_op")
    
    if not all([cookie_view, cookie_op]):
        print_fail("Missing sessions")
        results.add_fail("History permissions", "Missing sessions")
        return
    
    # Test 1: tst_view cannot access history (403)
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/istoric/contract/1",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 403:
            print_pass("tst_view history access returns 403")
            results.add_pass("History permissions - tst_view 403")
        else:
            print_fail(f"tst_view should return 403: status={resp.status_code}")
            results.add_fail("History permissions - tst_view 403", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"tst_view history test: {e}")
        results.add_fail("History permissions - tst_view 403", str(e))
    
    # Test 2: Anonymous returns 401/403
    try:
        resp = requests.get(f"{BASE_URL}/admin/istoric/contract/1", timeout=10)
        if resp.status_code in [401, 403]:
            print_pass(f"Anonymous history access returns {resp.status_code}")
            results.add_pass("History permissions - anonymous blocked")
        else:
            print_fail(f"Anonymous should return 401/403: status={resp.status_code}")
            results.add_fail("History permissions - anonymous blocked", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Anonymous history test: {e}")
        results.add_fail("History permissions - anonymous blocked", str(e))
    
    # Test 3: Unknown entity returns 404
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/istoric/unknown_entity/1",
            cookies={"cim_session": cookie_op},
            timeout=10
        )
        if resp.status_code == 404:
            print_pass("Unknown entity type returns 404")
            results.add_pass("History permissions - unknown entity 404")
        else:
            print_fail(f"Unknown entity should return 404: status={resp.status_code}")
            results.add_fail("History permissions - unknown entity 404", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Unknown entity test: {e}")
        results.add_fail("History permissions - unknown entity 404", str(e))

def test_4b_history_contract_creation():
    """Test 4B: History for created contracts"""
    print_test("4B. History/Audit - Contract Creation Events")
    
    cookie_op = results.sessions.get("tst_op")
    if not cookie_op:
        print_fail("No session for tst_op")
        results.add_fail("History contract creation", "No session")
        return
    
    if not results.created_contracts:
        print_fail("No contracts created in previous tests")
        results.add_fail("History contract creation", "No contracts")
        return
    
    contract_id = results.created_contracts[0]
    
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/istoric/contract/{contract_id}",
            cookies={"cim_session": cookie_op},
            timeout=10
        )
        if resp.status_code == 200:
            # Check for history-panel
            if "history-panel" in resp.text:
                print_pass(f"History panel present for contract {contract_id}")
                results.add_pass("History contract - panel present")
                
                # Check for history-item entries
                if "history-item" in resp.text:
                    print_pass(f"History items present for contract {contract_id}")
                    results.add_pass("History contract - items present")
                    
                    # Check for web_create_contract event
                    if "web_create_contract" in resp.text:
                        print_pass("History contains web_create_contract event")
                        results.add_pass("History contract - web_create_contract")
                    else:
                        print_fail("History missing web_create_contract event")
                        results.add_fail("History contract - web_create_contract", "Missing")
                    
                    # Check for contracte.create event
                    if "contracte.create" in resp.text or "contract.create" in resp.text:
                        print_pass("History contains contracte.create event")
                        results.add_pass("History contract - contracte.create")
                    else:
                        print_fail("History missing contracte.create event")
                        results.add_fail("History contract - contracte.create", "Missing")
                    
                    # Check for actor tst_op
                    if "tst_op" in resp.text:
                        print_pass("History shows actor tst_op")
                        results.add_pass("History contract - actor tst_op")
                    else:
                        print_fail("History missing actor tst_op")
                        results.add_fail("History contract - actor tst_op", "Missing")
                else:
                    print_fail("History items not found")
                    results.add_fail("History contract - items present", "Missing")
            else:
                print_fail("History panel not found")
                results.add_fail("History contract - panel present", "Missing")
        else:
            print_fail(f"GET history failed: status={resp.status_code}")
            results.add_fail("History contract - panel present", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"History contract test: {e}")
        results.add_fail("History contract - panel present", str(e))

def test_4c_history_attachment_events():
    """Test 4C: History for attachment events"""
    print_test("4C. History/Audit - Attachment Events")
    
    cookie_op = results.sessions.get("tst_op")
    if not cookie_op:
        print_fail("No session for tst_op")
        results.add_fail("History attachment", "No session")
        return
    
    if not results.created_contracts:
        print_fail("No contracts created in previous tests")
        results.add_fail("History attachment", "No contracts")
        return
    
    contract_id = results.created_contracts[0]
    
    # Upload a PDF attachment
    try:
        pdf_data = create_minimal_pdf()
        files = {"files": ("history_test.pdf", io.BytesIO(pdf_data), "application/pdf")}
        data = {"doc_type": "contract_concesiune", "title": "History Test", "description": "Test"}
        resp = requests.post(
            f"{BASE_URL}/admin/att/contract/{contract_id}",
            files=files,
            data=data,
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        
        if resp.status_code == 200:
            print_pass(f"Uploaded attachment to contract {contract_id}")
            
            # Extract doc_id
            match = re.search(r'/admin/attfile/(\d+)', resp.text)
            if match:
                doc_id = match.group(1)
                print_info(f"Attachment doc_id: {doc_id}")
                
                # Check history for attachment.add event
                resp_history = requests.get(
                    f"{BASE_URL}/admin/istoric/contract/{contract_id}",
                    cookies={"cim_session": cookie_op},
                    timeout=10
                )
                
                if resp_history.status_code == 200:
                    if "attachment.add" in resp_history.text or "att.add" in resp_history.text:
                        print_pass("History contains attachment.add event")
                        results.add_pass("History attachment - add event")
                    else:
                        print_fail("History missing attachment.add event")
                        results.add_fail("History attachment - add event", "Missing")
                    
                    # Delete the attachment
                    resp_delete = requests.post(
                        f"{BASE_URL}/admin/attdel/{doc_id}",
                        cookies={"cim_session": cookie_op},
                        headers=same_origin_headers(),
                        timeout=10
                    )
                    
                    if resp_delete.status_code == 200:
                        print_pass(f"Deleted attachment {doc_id}")
                        
                        # Check history for attachment.delete event
                        resp_history2 = requests.get(
                            f"{BASE_URL}/admin/istoric/contract/{contract_id}",
                            cookies={"cim_session": cookie_op},
                            timeout=10
                        )
                        
                        if resp_history2.status_code == 200:
                            if "attachment.delete" in resp_history2.text or "att.delete" in resp_history2.text:
                                print_pass("History contains attachment.delete event")
                                results.add_pass("History attachment - delete event")
                            else:
                                print_fail("History missing attachment.delete event")
                                results.add_fail("History attachment - delete event", "Missing")
                        else:
                            print_fail(f"GET history after delete failed: status={resp_history2.status_code}")
                            results.add_fail("History attachment - delete event", f"Status {resp_history2.status_code}")
                    else:
                        print_fail(f"Delete attachment failed: status={resp_delete.status_code}")
                        results.add_fail("History attachment - delete", f"Status {resp_delete.status_code}")
                else:
                    print_fail(f"GET history failed: status={resp_history.status_code}")
                    results.add_fail("History attachment - add event", f"Status {resp_history.status_code}")
            else:
                print_fail("Could not extract doc_id from upload response")
                results.add_fail("History attachment - extract doc_id", "Not found")
        else:
            print_fail(f"Upload attachment failed: status={resp.status_code}")
            results.add_fail("History attachment - upload", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"History attachment test: {e}")
        results.add_fail("History attachment - upload", str(e))

def test_4d_history_no_secrets():
    """Test 4D: History does not contain secrets"""
    print_test("4D. History/Audit - No Secrets")
    
    cookie_op = results.sessions.get("tst_op")
    if not cookie_op:
        print_fail("No session for tst_op")
        results.add_fail("History no secrets", "No session")
        return
    
    if not results.created_contracts:
        print_fail("No contracts created in previous tests")
        results.add_fail("History no secrets", "No contracts")
        return
    
    contract_id = results.created_contracts[0]
    
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/istoric/contract/{contract_id}",
            cookies={"cim_session": cookie_op},
            timeout=10
        )
        if resp.status_code == 200:
            text_lower = resp.text.lower()
            
            # Check for common secret patterns
            secrets_found = []
            if "password" in text_lower:
                secrets_found.append("password")
            if "token" in text_lower:
                secrets_found.append("token")
            if "cookie" in text_lower:
                secrets_found.append("cookie")
            if "database_url" in text_lower:
                secrets_found.append("DATABASE_URL")
            if "supabase" in text_lower and "secret" in text_lower:
                secrets_found.append("Supabase secret")
            
            if not secrets_found:
                print_pass("History does not contain secrets")
                results.add_pass("History no secrets")
            else:
                print_fail(f"History contains potential secrets: {', '.join(secrets_found)}")
                results.add_fail("History no secrets", f"Found: {', '.join(secrets_found)}")
        else:
            print_fail(f"GET history failed: status={resp.status_code}")
            results.add_fail("History no secrets", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"History no secrets test: {e}")
        results.add_fail("History no secrets", str(e))

def test_4e_history_pdf_generate():
    """Test 4E: History for PDF generation"""
    print_test("4E. History/Audit - PDF Generation Event")
    
    cookie_op = results.sessions.get("tst_op")
    if not cookie_op:
        print_fail("No session for tst_op")
        results.add_fail("History PDF generate", "No session")
        return
    
    if not results.created_contracts:
        print_fail("No contracts created in previous tests")
        results.add_fail("History PDF generate", "No contracts")
        return
    
    contract_id = results.created_contracts[0]
    
    # Open PDF to trigger pdf.generate event
    try:
        resp_pdf = requests.get(
            f"{BASE_URL}/admin/pdf/contract/{contract_id}",
            cookies={"cim_session": cookie_op},
            timeout=10
        )
        
        if resp_pdf.status_code == 200:
            print_pass(f"Opened PDF for contract {contract_id}")
            
            # Check history for pdf.generate event
            resp_history = requests.get(
                f"{BASE_URL}/admin/istoric/contract/{contract_id}",
                cookies={"cim_session": cookie_op},
                timeout=10
            )
            
            if resp_history.status_code == 200:
                if "pdf.generate" in resp_history.text:
                    print_pass("History contains pdf.generate event")
                    results.add_pass("History PDF - generate event")
                    
                    # Check for target contract:<id>
                    if f"contract:{contract_id}" in resp_history.text or f"contract {contract_id}" in resp_history.text:
                        print_pass(f"History shows target contract:{contract_id}")
                        results.add_pass("History PDF - target contract")
                    else:
                        print_fail(f"History missing target contract:{contract_id}")
                        results.add_fail("History PDF - target contract", "Missing")
                else:
                    print_fail(f"History missing pdf.generate event. History content: {resp_history.text[:1000]}")
                    results.add_fail("History PDF - generate event", "Missing")
            else:
                print_fail(f"GET history failed: status={resp_history.status_code}")
                results.add_fail("History PDF - generate event", f"Status {resp_history.status_code}")
        else:
            print_fail(f"Open PDF failed: status={resp_pdf.status_code}")
            results.add_fail("History PDF - open", f"Status {resp_pdf.status_code}")
    except Exception as e:
        print_fail(f"History PDF generate test: {e}")
        results.add_fail("History PDF - open", str(e))

def test_4f_history_ui_elements():
    """Test 4F: History UI elements"""
    print_test("4F. History/Audit - UI Elements")
    
    cookie_view = results.sessions.get("tst_view")
    cookie_op = results.sessions.get("tst_op")
    
    if not all([cookie_view, cookie_op]):
        print_fail("Missing sessions")
        results.add_fail("History UI", "Missing sessions")
        return
    
    if not results.created_contracts:
        print_fail("No contracts created in previous tests")
        results.add_fail("History UI", "No contracts")
        return
    
    contract_id = results.created_contracts[0]
    
    # Test 1: Attachment panel for tst_op shows history-open
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/att/contract/{contract_id}",
            cookies={"cim_session": cookie_op},
            timeout=10
        )
        if resp.status_code == 200:
            if "history-open" in resp.text:
                print_pass("tst_op attachment panel has history-open")
                results.add_pass("History UI - tst_op history-open")
            else:
                print_fail("tst_op attachment panel missing history-open")
                results.add_fail("History UI - tst_op history-open", "Missing")
        else:
            print_fail(f"GET attachment panel failed: status={resp.status_code}")
            results.add_fail("History UI - tst_op history-open", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"tst_op attachment panel test: {e}")
        results.add_fail("History UI - tst_op history-open", str(e))
    
    # Test 2: Attachment panel for tst_view does NOT show history-open
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/att/contract/{contract_id}",
            cookies={"cim_session": cookie_view},
            timeout=10
        )
        if resp.status_code == 200:
            if "history-open" not in resp.text:
                print_pass("tst_view attachment panel does not have history-open")
                results.add_pass("History UI - tst_view no history-open")
            else:
                print_fail("tst_view attachment panel should not have history-open")
                results.add_fail("History UI - tst_view no history-open", "Present")
        else:
            print_fail(f"GET attachment panel failed: status={resp.status_code}")
            results.add_fail("History UI - tst_view no history-open", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"tst_view attachment panel test: {e}")
        results.add_fail("History UI - tst_view no history-open", str(e))

# ============================================================================
# (D) REGRESSION TESTS
# ============================================================================

def test_5a_regression_cerere_status():
    """Test 5A: Regression - cerere status validation"""
    print_test("5A. Regression - Cerere Status Validation")
    
    cookie_op = results.sessions.get("tst_op")
    if not cookie_op:
        print_fail("No session for tst_op")
        results.add_fail("Regression cerere status", "No session")
        return
    
    # Test invalid status returns 422
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/ui/cerere/3/status",
            data={"status": "invalid_status", "note": "Test"},
            cookies={"cim_session": cookie_op},
            headers=same_origin_headers(),
            timeout=10
        )
        if resp.status_code == 422:
            print_pass("Invalid cerere status returns 422")
            results.add_pass("Regression - cerere invalid status")
        else:
            print_fail(f"Invalid status should return 422: status={resp.status_code}")
            results.add_fail("Regression - cerere invalid status", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Cerere invalid status test: {e}")
        results.add_fail("Regression - cerere invalid status", str(e))

def test_5b_regression_logout():
    """Test 5B: Regression - logout behavior"""
    print_test("5B. Regression - Logout Behavior")
    
    # Login a fresh session
    cookie = login("tst_admin", "Tst-Mobil-4821x")
    if not cookie:
        print_fail("Failed to login for logout test")
        results.add_fail("Regression logout", "Login failed")
        return
    
    # Test 1: POST /admin/logout with same-origin headers revokes session
    try:
        resp = requests.post(
            f"{BASE_URL}/admin/logout",
            cookies={"cim_session": cookie},
            headers=same_origin_headers(),
            allow_redirects=False,
            timeout=10
        )
        if resp.status_code == 303:
            print_pass("POST /admin/logout returns 303")
            results.add_pass("Regression logout - POST 303")
            
            # Verify old cookie is rejected
            resp2 = requests.get(
                f"{BASE_URL}/admin/",
                cookies={"cim_session": cookie},
                allow_redirects=False,
                timeout=10
            )
            if resp2.status_code == 303:
                print_pass("Old cookie rejected after logout")
                results.add_pass("Regression logout - cookie rejected")
            else:
                print_fail(f"Old cookie should be rejected: status={resp2.status_code}")
                results.add_fail("Regression logout - cookie rejected", f"Status {resp2.status_code}")
        else:
            print_fail(f"POST /admin/logout should return 303: status={resp.status_code}")
            results.add_fail("Regression logout - POST 303", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Logout POST test: {e}")
        results.add_fail("Regression logout - POST 303", str(e))
    
    # Test 2: GET /admin/logout is harmless
    cookie2 = login("tst_admin", "Tst-Mobil-4821x")
    if cookie2:
        try:
            resp = requests.get(
                f"{BASE_URL}/admin/logout",
                cookies={"cim_session": cookie2},
                allow_redirects=False,
                timeout=10
            )
            if resp.status_code == 303:
                # Verify session still valid
                resp2 = requests.get(
                    f"{BASE_URL}/admin/",
                    cookies={"cim_session": cookie2},
                    allow_redirects=False,
                    timeout=10
                )
                if resp2.status_code == 200:
                    print_pass("GET /admin/logout is harmless, session still valid")
                    results.add_pass("Regression logout - GET harmless")
                else:
                    print_fail(f"Session should still be valid: status={resp2.status_code}")
                    results.add_fail("Regression logout - GET harmless", f"Status {resp2.status_code}")
                
                # Clean up: logout properly
                requests.post(
                    f"{BASE_URL}/admin/logout",
                    cookies={"cim_session": cookie2},
                    headers=same_origin_headers(),
                    timeout=10
                )
            else:
                print_fail(f"GET /admin/logout should return 303: status={resp.status_code}")
                results.add_fail("Regression logout - GET harmless", f"Status {resp.status_code}")
        except Exception as e:
            print_fail(f"Logout GET test: {e}")
            results.add_fail("Regression logout - GET harmless", str(e))

def test_5c_regression_search():
    """Test 5C: Regression - search functionality"""
    print_test("5C. Regression - Search Functionality")
    
    cookie_op = results.sessions.get("tst_op")
    if not cookie_op:
        print_fail("No session for tst_op")
        results.add_fail("Regression search", "No session")
        return
    
    # Test search for "popescu"
    try:
        resp = requests.get(
            f"{BASE_URL}/admin/cauta?q=popescu",
            cookies={"cim_session": cookie_op},
            timeout=10
        )
        if resp.status_code == 200:
            print_pass("Search for 'popescu' works")
            results.add_pass("Regression - search works")
        else:
            print_fail(f"Search failed: status={resp.status_code}")
            results.add_fail("Regression - search works", f"Status {resp.status_code}")
    except Exception as e:
        print_fail(f"Search test: {e}")
        results.add_fail("Regression - search works", str(e))

def test_6_check_backend_logs():
    """Test 6: Check backend logs for 500 errors"""
    print_test("6. Check Backend Logs")
    
    try:
        import subprocess
        result = subprocess.run(
            ["tail", "-n", "200", "/var/log/supervisor/backend.err.log"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            log_content = result.stdout
            # Filter out common non-error patterns
            lines = log_content.split('\n')
            error_lines = []
            for line in lines:
                if "500" in line or ("ERROR" in line and "INFO" not in line) or ("Exception" in line and "INFO" not in line):
                    error_lines.append(line)
            
            if error_lines:
                print_fail(f"Backend logs contain errors:\n" + '\n'.join(error_lines[-20:]))
                results.add_fail("Backend logs", "Errors found")
            else:
                print_pass("No 500 errors in backend logs")
                results.add_pass("Backend logs clean")
        else:
            print_info("Could not read backend logs")
            results.add_pass("Backend logs (skipped)")
    except Exception as e:
        print_info(f"Could not check backend logs: {e}")
        results.add_pass("Backend logs (skipped)")

def test_7_logout_all_sessions():
    """Test 7: Logout all test sessions"""
    print_test("7. Logout All Sessions")
    
    for username, cookie in results.sessions.items():
        try:
            resp = requests.post(
                f"{BASE_URL}/admin/logout",
                cookies={"cim_session": cookie},
                headers=same_origin_headers(),
                allow_redirects=False,
                timeout=10
            )
            if resp.status_code == 303:
                print_pass(f"Logged out {username}")
                results.add_pass(f"Logout {username}")
            else:
                print_fail(f"Logout {username} failed: status={resp.status_code}")
                results.add_fail(f"Logout {username}", f"Status {resp.status_code}")
        except Exception as e:
            print_fail(f"Logout {username} exception: {e}")
            results.add_fail(f"Logout {username}", str(e))

def main():
    """Run all tests"""
    print(f"\n{BLUE}{'='*80}{RESET}")
    print(f"{BLUE}Cimitir Phase-3 Features Tests{RESET}")
    print(f"{BLUE}Base URL: {BASE_URL}{RESET}")
    print(f"{BLUE}{'='*80}{RESET}")
    
    # Login
    test_1_login_all_users()
    
    # (A) Contract Creation
    test_2a_contract_creation_permissions()
    test_2b_contract_creation_validation()
    test_2c_contract_creation_success()
    test_2d_contract_creation_verification()
    test_2e_contract_creation_ui()
    
    # (B) PDF Documents
    test_3a_pdf_contract()
    test_3b_pdf_other_documents()
    test_3c_pdf_validation()
    test_3d_pdf_ui_elements()
    
    # (C) History/Audit
    test_4a_history_permissions()
    test_4b_history_contract_creation()
    test_4c_history_attachment_events()
    test_4d_history_no_secrets()
    test_4e_history_pdf_generate()
    test_4f_history_ui_elements()
    
    # (D) Regression
    test_5a_regression_cerere_status()
    test_5b_regression_logout()
    test_5c_regression_search()
    
    # Check logs and logout
    test_6_check_backend_logs()
    test_7_logout_all_sessions()
    
    success = results.summary()
    return 0 if success else 1

if __name__ == "__main__":
    exit(main())
