#!/usr/bin/env python3
"""
Targeted re-test for contract creation date validation bug fix.
Bug: Invalid dates were silently replaced with today's date.
Fix: Invalid dates must return 422 with specific Romanian error messages.
"""

import requests
import sys
import re
from typing import Dict, Optional, Tuple

BASE_URL = "http://localhost:8001"
SESSION = requests.Session()

# Test credentials
USERNAME = "tst_op"
PASSWORD = "Tst-Mobil-4821x"

# CSRF headers for state-changing POSTs
CSRF_HEADERS = {
    "Origin": BASE_URL,
    "Sec-Fetch-Site": "same-origin"
}

def login() -> bool:
    """Login and get session cookie."""
    print(f"\n{'='*80}")
    print("LOGIN")
    print(f"{'='*80}")
    
    resp = SESSION.post(
        f"{BASE_URL}/admin/access/login",
        data={
            "source_id": "cimitir",
            "username": USERNAME,
            "password": PASSWORD
        },
        allow_redirects=False
    )
    
    print(f"POST /admin/access/login -> {resp.status_code}")
    
    if resp.status_code == 303:
        cookie = SESSION.cookies.get("cim_session")
        if cookie:
            print(f"✓ Login successful, cookie: cim_session={cookie[:20]}...")
            return True
        else:
            print("✗ No cim_session cookie received")
            return False
    else:
        print(f"✗ Expected 303, got {resp.status_code}")
        print(f"Response: {resp.text[:200]}")
        return False

def logout() -> None:
    """Logout at the end."""
    print(f"\n{'='*80}")
    print("LOGOUT")
    print(f"{'='*80}")
    
    resp = SESSION.post(
        f"{BASE_URL}/admin/logout",
        headers=CSRF_HEADERS,
        allow_redirects=False
    )
    print(f"POST /admin/logout -> {resp.status_code}")

def check_error_message(html: str, expected_msg: str) -> bool:
    """Check if drawer-error contains expected Romanian error message."""
    # Look for data-testid="drawer-error" element
    pattern = r'data-testid=["\']drawer-error["\'][^>]*>(.*?)</div>'
    match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
    
    if match:
        error_content = match.group(1)
        # Clean up HTML tags and whitespace
        error_text = re.sub(r'<[^>]+>', '', error_content).strip()
        if expected_msg in error_text:
            return True
        else:
            print(f"  ✗ Error message mismatch:")
            print(f"    Expected: '{expected_msg}'")
            print(f"    Found: '{error_text}'")
            return False
    else:
        print(f"  ✗ No drawer-error element found in response")
        # Print a snippet of the response for debugging
        print(f"  Response snippet: {html[:500]}")
        return False

def test_invalid_date_contract(
    cod_loc: str,
    numar_contract: str,
    data_contract: str,
    data_inceput: str,
    data_sfarsit: str,
    expected_error: str,
    scenario_name: str
) -> Tuple[bool, int, str]:
    """
    Test contract creation with invalid date.
    Returns (pass, status_code, error_message).
    """
    print(f"\n{'-'*80}")
    print(f"SCENARIO: {scenario_name}")
    print(f"{'-'*80}")
    
    payload = {
        "numar_contract": numar_contract,
        "nume": "Retest",
        "prenume": "Titular",
        "cnp": "1900101123456",
        "data_contract": data_contract,
        "data_inceput": data_inceput,
        "data_sfarsit": data_sfarsit
    }
    
    print(f"POST /admin/ui/loc/{cod_loc}/contract")
    print(f"  Payload: {payload}")
    
    resp = SESSION.post(
        f"{BASE_URL}/admin/ui/loc/{cod_loc}/contract",
        data=payload,
        headers=CSRF_HEADERS,
        allow_redirects=False
    )
    
    print(f"  Response: {resp.status_code}")
    
    if resp.status_code == 422:
        print(f"  ✓ Got expected 422 status")
        
        # Check for expected error message
        if check_error_message(resp.text, expected_error):
            print(f"  ✓ Error message correct: '{expected_error}'")
            return (True, resp.status_code, expected_error)
        else:
            return (False, resp.status_code, "Error message mismatch")
    else:
        print(f"  ✗ Expected 422, got {resp.status_code}")
        if resp.status_code == 200:
            print(f"  ✗ CONTRACT WAS CREATED (BUG NOT FIXED!)")
        return (False, resp.status_code, f"Expected 422, got {resp.status_code}")

def verify_no_contracts(cod_loc: str) -> Tuple[bool, str]:
    """Verify that no contracts exist for the given place."""
    print(f"\n{'-'*80}")
    print(f"VERIFY: No contracts for {cod_loc}")
    print(f"{'-'*80}")
    
    # Check via GET /admin/contracte
    resp1 = SESSION.get(f"{BASE_URL}/admin/contracte")
    print(f"GET /admin/contracte -> {resp1.status_code}")
    
    rt_contracts = re.findall(r'RT-\w+', resp1.text)
    print(f"  Found RT- contracts: {rt_contracts}")
    
    # Check via GET /admin/ui/loc/{cod_loc}
    resp2 = SESSION.get(f"{BASE_URL}/admin/ui/loc/{cod_loc}")
    print(f"GET /admin/ui/loc/{cod_loc} -> {resp2.status_code}")
    
    if resp2.status_code == 200:
        # Look for contract indicators in the response
        has_contract = "contract-item" in resp2.text or "pdf-contract" in resp2.text
        if has_contract:
            print(f"  ✗ Place {cod_loc} appears to have a contract")
            return (False, f"Place {cod_loc} has a contract")
        else:
            print(f"  ✓ Place {cod_loc} has no contract")
            return (True, "No contract found")
    else:
        print(f"  ✗ Unexpected status {resp2.status_code}")
        return (False, f"Unexpected status {resp2.status_code}")

def test_valid_contract_new_concesionar(
    cod_loc: str,
    numar_contract: str,
    data_contract: str,
    data_inceput: str,
    data_sfarsit: str
) -> Tuple[bool, Optional[str]]:
    """
    Test valid contract creation with NEW concesionar.
    Returns (pass, contract_id).
    """
    print(f"\n{'-'*80}")
    print(f"SCENARIO: Valid contract creation on {cod_loc} with NEW concesionar")
    print(f"{'-'*80}")
    
    payload = {
        "numar_contract": numar_contract,
        "nume": "Retest",
        "prenume": "Titular",
        "cnp": "1900101123456",
        "data_contract": data_contract,
        "data_inceput": data_inceput,
        "data_sfarsit": data_sfarsit
    }
    
    print(f"POST /admin/ui/loc/{cod_loc}/contract")
    print(f"  Payload: {payload}")
    
    resp = SESSION.post(
        f"{BASE_URL}/admin/ui/loc/{cod_loc}/contract",
        data=payload,
        headers=CSRF_HEADERS,
        allow_redirects=False
    )
    
    print(f"  Response: {resp.status_code}")
    
    if resp.status_code == 200:
        print(f"  ✓ Got expected 200 status")
        
        # Check for contract number in response
        if numar_contract in resp.text:
            print(f"  ✓ Contract number '{numar_contract}' found in response")
        else:
            print(f"  ✗ Contract number '{numar_contract}' NOT found in response")
            return (False, None)
        
        # Extract PDF link /admin/pdf/contract/<id>
        pdf_match = re.search(r'/admin/pdf/contract/(\d+)', resp.text)
        if pdf_match:
            contract_id = pdf_match.group(1)
            print(f"  ✓ PDF link found: /admin/pdf/contract/{contract_id}")
            
            # Verify PDF download
            pdf_resp = SESSION.get(f"{BASE_URL}/admin/pdf/contract/{contract_id}")
            print(f"  GET /admin/pdf/contract/{contract_id} -> {pdf_resp.status_code}")
            
            if pdf_resp.status_code == 200:
                if pdf_resp.content[:4] == b'%PDF':
                    print(f"  ✓ PDF is valid (starts with %PDF)")
                    return (True, contract_id)
                else:
                    print(f"  ✗ PDF is invalid (doesn't start with %PDF)")
                    return (False, contract_id)
            else:
                print(f"  ✗ PDF download failed with {pdf_resp.status_code}")
                return (False, contract_id)
        else:
            print(f"  ✗ No PDF link found in response")
            return (False, None)
    else:
        print(f"  ✗ Expected 200, got {resp.status_code}")
        print(f"  Response snippet: {resp.text[:500]}")
        return (False, None)

def test_valid_contract_existing_concesionar(
    cod_loc: str,
    numar_contract: str,
    concesionar_id: int
) -> Tuple[bool, Optional[str]]:
    """
    Test valid contract creation with existing concesionar_id.
    All dates empty (defaults to today).
    Returns (pass, contract_id).
    """
    print(f"\n{'-'*80}")
    print(f"SCENARIO: Valid contract creation on {cod_loc} with concesionar_id={concesionar_id}")
    print(f"{'-'*80}")
    
    payload = {
        "numar_contract": numar_contract,
        "concesionar_id": str(concesionar_id),
        "data_contract": "",
        "data_inceput": "",
        "data_sfarsit": ""
    }
    
    print(f"POST /admin/ui/loc/{cod_loc}/contract")
    print(f"  Payload: {payload}")
    
    resp = SESSION.post(
        f"{BASE_URL}/admin/ui/loc/{cod_loc}/contract",
        data=payload,
        headers=CSRF_HEADERS,
        allow_redirects=False
    )
    
    print(f"  Response: {resp.status_code}")
    
    if resp.status_code == 200:
        print(f"  ✓ Got expected 200 status")
        
        # Check for contract number in response
        if numar_contract in resp.text:
            print(f"  ✓ Contract number '{numar_contract}' found in response")
        else:
            print(f"  ✗ Contract number '{numar_contract}' NOT found in response")
            return (False, None)
        
        # Extract PDF link
        pdf_match = re.search(r'/admin/pdf/contract/(\d+)', resp.text)
        if pdf_match:
            contract_id = pdf_match.group(1)
            print(f"  ✓ PDF link found: /admin/pdf/contract/{contract_id}")
            return (True, contract_id)
        else:
            print(f"  ✗ No PDF link found in response")
            return (False, None)
    else:
        print(f"  ✗ Expected 200, got {resp.status_code}")
        print(f"  Response snippet: {resp.text[:500]}")
        return (False, None)

def test_contract_history(contract_id: str) -> bool:
    """Test that contract history contains expected entries."""
    print(f"\n{'-'*80}")
    print(f"SCENARIO: Contract history for contract/{contract_id}")
    print(f"{'-'*80}")
    
    resp = SESSION.get(f"{BASE_URL}/admin/istoric/contract/{contract_id}")
    print(f"GET /admin/istoric/contract/{contract_id} -> {resp.status_code}")
    
    if resp.status_code == 200:
        print(f"  ✓ Got expected 200 status")
        
        # Check for history-item entries
        history_items = re.findall(r'history-item', resp.text)
        print(f"  Found {len(history_items)} history-item entries")
        
        # Check for web_create_contract
        if 'web_create_contract' in resp.text:
            print(f"  ✓ Found 'web_create_contract' event")
        else:
            print(f"  ✗ Missing 'web_create_contract' event")
            return False
        
        # Check for contracte.create
        if 'contracte.create' in resp.text:
            print(f"  ✓ Found 'contracte.create' event")
        else:
            print(f"  ✗ Missing 'contracte.create' event")
            return False
        
        # Check for actor tst_op
        if 'tst_op' in resp.text:
            print(f"  ✓ Found actor 'tst_op'")
        else:
            print(f"  ✗ Missing actor 'tst_op'")
            return False
        
        return True
    else:
        print(f"  ✗ Expected 200, got {resp.status_code}")
        return False

def test_duplicate_contract(cod_loc: str, numar_contract: str) -> bool:
    """Test that creating a second contract on the same place returns 422."""
    print(f"\n{'-'*80}")
    print(f"SCENARIO: Duplicate contract on {cod_loc}")
    print(f"{'-'*80}")
    
    payload = {
        "numar_contract": numar_contract,
        "nume": "Duplicate",
        "prenume": "Test",
        "cnp": "2900101123456",
        "data_contract": "15.03.2026",
        "data_inceput": "",
        "data_sfarsit": "15.03.2056"
    }
    
    print(f"POST /admin/ui/loc/{cod_loc}/contract")
    print(f"  Payload: {payload}")
    
    resp = SESSION.post(
        f"{BASE_URL}/admin/ui/loc/{cod_loc}/contract",
        data=payload,
        headers=CSRF_HEADERS,
        allow_redirects=False
    )
    
    print(f"  Response: {resp.status_code}")
    
    if resp.status_code == 422:
        print(f"  ✓ Got expected 422 status")
        
        # Check for 'contract activ' error
        if 'contract activ' in resp.text.lower():
            print(f"  ✓ Error message contains 'contract activ'")
            return True
        else:
            print(f"  ✗ Error message doesn't contain 'contract activ'")
            print(f"  Response snippet: {resp.text[:500]}")
            return False
    else:
        print(f"  ✗ Expected 422, got {resp.status_code}")
        return False

def main():
    """Run all test scenarios."""
    print(f"\n{'#'*80}")
    print("# TARGETED RE-TEST: Contract Date Validation Bug Fix")
    print(f"{'#'*80}")
    
    results = []
    
    # Login
    if not login():
        print("\n✗ LOGIN FAILED - Cannot proceed with tests")
        sys.exit(1)
    
    # Scenario 1: data_contract='azi'
    result = test_invalid_date_contract(
        cod_loc="TEST-MOB-6",
        numar_contract="RT-1",
        data_contract="azi",
        data_inceput="01.01.2025",
        data_sfarsit="01.01.2055",
        expected_error="Data contractului nu este validă",
        scenario_name="1) data_contract='azi' -> 422"
    )
    results.append(("Scenario 1: data_contract='azi'", result[0]))
    
    # Scenario 2: data_contract='31.31.2050'
    result = test_invalid_date_contract(
        cod_loc="TEST-MOB-6",
        numar_contract="RT-2",
        data_contract="31.31.2050",
        data_inceput="01.01.2025",
        data_sfarsit="01.01.2055",
        expected_error="Data contractului nu este validă",
        scenario_name="2) data_contract='31.31.2050' -> 422"
    )
    results.append(("Scenario 2: data_contract='31.31.2050'", result[0]))
    
    # Scenario 3: data_inceput='99.99.2020'
    result = test_invalid_date_contract(
        cod_loc="TEST-MOB-6",
        numar_contract="RT-3",
        data_contract="15.03.2026",
        data_inceput="99.99.2020",
        data_sfarsit="01.01.2055",
        expected_error="Data de început nu este validă",
        scenario_name="3) data_inceput='99.99.2020' -> 422"
    )
    results.append(("Scenario 3: data_inceput='99.99.2020'", result[0]))
    
    # Scenario 4: data_sfarsit='xx'
    result = test_invalid_date_contract(
        cod_loc="TEST-MOB-6",
        numar_contract="RT-4",
        data_contract="15.03.2026",
        data_inceput="01.01.2025",
        data_sfarsit="xx",
        expected_error="Data de sfârșit nu este validă",
        scenario_name="4) data_sfarsit='xx' -> 422"
    )
    results.append(("Scenario 4: data_sfarsit='xx'", result[0]))
    
    # Scenario 5: data_sfarsit earlier than data_inceput
    result = test_invalid_date_contract(
        cod_loc="TEST-MOB-6",
        numar_contract="RT-5",
        data_contract="15.03.2026",
        data_inceput="01.01.2030",
        data_sfarsit="01.01.2020",
        expected_error="",  # Any 422 is acceptable
        scenario_name="5) data_sfarsit earlier than data_inceput -> 422"
    )
    # For this scenario, we just need 422, not a specific message
    if result[1] == 422:
        results.append(("Scenario 5: end before start", True))
    else:
        results.append(("Scenario 5: end before start", False))
    
    # Scenario 6: Verify no contracts exist for TEST-MOB-6
    verify_result = verify_no_contracts("TEST-MOB-6")
    results.append(("Scenario 6: No contracts for TEST-MOB-6", verify_result[0]))
    
    # Scenario 7: Valid creation on TEST-MOB-6 with NEW concesionar
    valid_result = test_valid_contract_new_concesionar(
        cod_loc="TEST-MOB-6",
        numar_contract="RT-OK-1",
        data_contract="15.03.2026",
        data_inceput="",  # Empty => defaults to contract date
        data_sfarsit="15.03.2056"
    )
    results.append(("Scenario 7: Valid contract on TEST-MOB-6", valid_result[0]))
    contract_id_1 = valid_result[1]
    
    # Scenario 8: Valid creation on TEST-MOB-7 with concesionar_id=1
    valid_result_2 = test_valid_contract_existing_concesionar(
        cod_loc="TEST-MOB-7",
        numar_contract="RT-OK-2",
        concesionar_id=1
    )
    results.append(("Scenario 8: Valid contract on TEST-MOB-7", valid_result_2[0]))
    
    # Scenario 9: Check history for RT-OK-1
    if contract_id_1:
        history_result = test_contract_history(contract_id_1)
        results.append(("Scenario 9: Contract history", history_result))
    else:
        results.append(("Scenario 9: Contract history", False))
        print("\n  ✗ Skipped (no contract_id from scenario 7)")
    
    # Scenario 10: Duplicate contract on TEST-MOB-6
    duplicate_result = test_duplicate_contract("TEST-MOB-6", "RT-DUP-1")
    results.append(("Scenario 10: Duplicate contract -> 422", duplicate_result))
    
    # Logout
    logout()
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for scenario, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {scenario}")
    
    print(f"\n{'='*80}")
    print(f"TOTAL: {passed}/{total} scenarios passed")
    print(f"{'='*80}")
    
    if passed == total:
        print("\n✓ ALL TESTS PASSED - Bug fix verified successfully!")
        sys.exit(0)
    else:
        print(f"\n✗ {total - passed} TEST(S) FAILED - Bug fix incomplete or regression detected")
        sys.exit(1)

if __name__ == "__main__":
    main()
