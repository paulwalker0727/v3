#!/usr/bin/env python3
"""
Test script for two new features:
1. Secure logout (server-side session invalidation)
2. Complete audit for entities (locuri_de_veci, concesionari, contracte, inhumati, plati)

All test data uses prefix 'TEST-QA-' and is cleaned up at the end.
"""
import json
import os
import sys
import time
import uuid
from urllib.parse import urlparse, parse_qs
import psycopg
import requests

# Load credentials
with open('/app/validation/.secrets.json') as f:
    CREDS = json.load(f)

STAFF_USER = CREDS['user']
STAFF_PASSWORD = CREDS['new_password']  # Already past forced password change
SYNC_KEY = CREDS['sync_key']
SYNC_CLIENT = 'main-qa0000002'

BASE_URL = 'http://127.0.0.1:8001'
ADMIN_URL = f'{BASE_URL}/admin'
API_URL = f'{BASE_URL}/api'

# Parse DATABASE_URL for read-only verification
with open('/app/backend/.env') as f:
    for line in f:
        if line.startswith('DATABASE_URL='):
            db_url = line.split('=', 1)[1].strip().strip('"')
            break

# Track test data for cleanup
test_data = {
    'concesionari_uids': [],
    'locuri_uids': [],
    'plati_ids': [],
    'contracte_ids': [],
    'inhumati_ids': []
}

def db_connect():
    """Connect to PostgreSQL for read-only verification"""
    return psycopg.connect(db_url)

def print_section(title):
    print(f"\n{'='*80}")
    print(f"  {title}")
    print('='*80)

def print_test(name, passed, details=""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status}: {name}")
    if details:
        print(f"  {details}")

# ============================================================================
# FEATURE 1: SECURE LOGOUT (SERVER-SIDE SESSION INVALIDATION)
# ============================================================================

def test_feature_1_secure_logout():
    print_section("FEATURE 1: SECURE LOGOUT (SERVER-SIDE SESSION INVALIDATION)")
    
    results = []
    
    # Step a: Log in twice (two independent sessions S1 and S2)
    print("\n[Step a] Creating two independent sessions...")
    
    s1 = requests.Session()
    s2 = requests.Session()
    
    # Login S1
    r1 = s1.post(f'{ADMIN_URL}/access/login', data={
        'source_id': 'cimitir',
        'username': STAFF_USER,
        'password': STAFF_PASSWORD
    }, allow_redirects=False)
    
    if r1.status_code != 303 or 'cim_session' not in r1.cookies:
        print_test("Login S1", False, f"Status: {r1.status_code}, Cookie: {'cim_session' in r1.cookies}")
        return results
    
    s1_cookie_value = r1.cookies['cim_session']
    print_test("Login S1", True, f"Cookie: {s1_cookie_value[:20]}...")
    
    # Login S2
    r2 = s2.post(f'{ADMIN_URL}/access/login', data={
        'source_id': 'cimitir',
        'username': STAFF_USER,
        'password': STAFF_PASSWORD
    }, allow_redirects=False)
    
    if r2.status_code != 303 or 'cim_session' not in r2.cookies:
        print_test("Login S2", False, f"Status: {r2.status_code}, Cookie: {'cim_session' in r2.cookies}")
        return results
    
    s2_cookie_value = r2.cookies['cim_session']
    print_test("Login S2", True, f"Cookie: {s2_cookie_value[:20]}...")
    results.append(("Login two sessions", True))
    
    # Step b: With S1, access protected pages
    print("\n[Step b] Testing S1 access to protected pages...")
    
    pages = ['/admin/system', '/admin/cloud', '/admin/']
    all_ok = True
    for page in pages:
        r = s1.get(f'{BASE_URL}{page}', allow_redirects=False)
        ok = r.status_code == 200
        print_test(f"S1 GET {page}", ok, f"Status: {r.status_code}")
        all_ok = all_ok and ok
    
    results.append(("S1 access before logout", all_ok))
    
    # Step c: Logout S1 and verify cookie deletion
    print("\n[Step c] Logout S1 and verify cookie deletion...")
    
    r = s1.get(f'{ADMIN_URL}/logout', allow_redirects=False)
    logout_ok = r.status_code == 303 and r.headers.get('location') == '/admin/access'
    print_test("Logout redirect", logout_ok, f"Status: {r.status_code}, Location: {r.headers.get('location')}")
    
    # Check Set-Cookie header for deletion
    set_cookie = r.headers.get('set-cookie', '')
    cookie_deleted = (
        'cim_session=' in set_cookie and
        ('Max-Age=0' in set_cookie or 'expires=' in set_cookie.lower()) and
        'Path=/' in set_cookie and
        'HttpOnly' in set_cookie and
        'SameSite=lax' in set_cookie
    )
    print_test("Cookie deletion", cookie_deleted, f"Set-Cookie: {set_cookie[:100]}...")
    results.append(("Logout cookie deletion", logout_ok and cookie_deleted))
    
    # Step d: Reuse old S1 cookie value on brand-new session
    print("\n[Step d] Testing revoked session with old cookie...")
    
    s1_new = requests.Session()
    s1_new.cookies.set('cim_session', s1_cookie_value, domain='127.0.0.1', path='/')
    
    # Test protected pages
    revoked_tests = []
    
    # GET /admin/system
    r = s1_new.get(f'{ADMIN_URL}/system', allow_redirects=False)
    rejected = r.status_code in (303, 401, 403)
    print_test("Revoked: GET /admin/system", rejected, f"Status: {r.status_code}")
    revoked_tests.append(rejected)
    
    # GET /admin/cloud
    r = s1_new.get(f'{ADMIN_URL}/cloud', allow_redirects=False)
    rejected = r.status_code in (303, 401, 403)
    print_test("Revoked: GET /admin/cloud", rejected, f"Status: {r.status_code}")
    revoked_tests.append(rejected)
    
    # GET /admin/
    r = s1_new.get(f'{ADMIN_URL}/', allow_redirects=False)
    rejected = r.status_code in (303, 401, 403)
    print_test("Revoked: GET /admin/", rejected, f"Status: {r.status_code}")
    revoked_tests.append(rejected)
    
    # GET /api/admin/map.json
    r = s1_new.get(f'{API_URL}/admin/map.json', allow_redirects=False)
    rejected = r.status_code in (401, 403)
    print_test("Revoked: GET /api/admin/map.json", rejected, f"Status: {r.status_code}")
    revoked_tests.append(rejected)
    
    # POST /admin/system/test-db
    r = s1_new.post(f'{ADMIN_URL}/system/test-db', 
                    headers={'Origin': 'http://127.0.0.1:8001', 'Sec-Fetch-Site': 'same-origin'},
                    allow_redirects=False)
    rejected = r.status_code in (303, 401, 403)
    print_test("Revoked: POST /admin/system/test-db", rejected, f"Status: {r.status_code}")
    revoked_tests.append(rejected)
    
    results.append(("Revoked session rejected", all(revoked_tests)))
    
    # Step e: S2 should still work
    print("\n[Step e] Testing S2 still works...")
    
    r = s2.get(f'{ADMIN_URL}/system', allow_redirects=False)
    s2_ok = r.status_code == 200
    print_test("S2 GET /admin/system", s2_ok, f"Status: {r.status_code}")
    results.append(("S2 still works", s2_ok))
    
    # New login should work
    s3 = requests.Session()
    r = s3.post(f'{ADMIN_URL}/access/login', data={
        'source_id': 'cimitir',
        'username': STAFF_USER,
        'password': STAFF_PASSWORD
    }, allow_redirects=False)
    new_login_ok = r.status_code == 303 and 'cim_session' in r.cookies
    print_test("New login works", new_login_ok, f"Status: {r.status_code}")
    results.append(("New login after logout", new_login_ok))
    
    # Step f: Database verification
    print("\n[Step f] Database verification...")
    
    conn = db_connect()
    cur = conn.cursor()
    
    # Check settings table for revoked_session keys
    cur.execute("SELECT COUNT(*) FROM settings WHERE key LIKE 'revoked_session:%'")
    revoked_count = cur.fetchone()[0]
    print_test("Revoked sessions in DB", revoked_count >= 1, f"Count: {revoked_count}")
    
    # Check audit_log for logout action
    cur.execute("""
        SELECT COUNT(*) FROM audit_log 
        WHERE action = 'logout' 
        AND actor = %s 
        AND ts > NOW() - INTERVAL '5 minutes'
    """, (STAFF_USER,))
    logout_audit_count = cur.fetchone()[0]
    print_test("Logout in audit_log", logout_audit_count >= 1, f"Count: {logout_audit_count}")
    
    # Check no secrets in audit_log detail
    cur.execute("""
        SELECT detail FROM audit_log 
        WHERE action = 'logout' 
        AND actor = %s 
        AND ts > NOW() - INTERVAL '5 minutes'
        LIMIT 1
    """, (STAFF_USER,))
    row = cur.fetchone()
    if row:
        detail = row[0]
        has_secrets = any(secret in detail for secret in [STAFF_PASSWORD, SYNC_KEY, 'postgresql://', 'sb_secret', 'eyJ'])
        print_test("No secrets in logout audit", not has_secrets, f"Detail length: {len(detail)}")
        results.append(("Logout audit no secrets", not has_secrets))
    
    conn.close()
    results.append(("Database verification", revoked_count >= 1 and logout_audit_count >= 1))
    
    # Step g: Logout without cookie and with revoked cookie
    print("\n[Step g] Logout without cookie and with revoked cookie...")
    
    # Without cookie
    s_no_cookie = requests.Session()
    r = s_no_cookie.get(f'{ADMIN_URL}/logout', allow_redirects=False)
    no_cookie_ok = r.status_code == 303
    print_test("Logout without cookie", no_cookie_ok, f"Status: {r.status_code}")
    
    # With revoked cookie
    s_revoked = requests.Session()
    s_revoked.cookies.set('cim_session', s1_cookie_value, domain='127.0.0.1', path='/')
    r = s_revoked.get(f'{ADMIN_URL}/logout', allow_redirects=False)
    revoked_logout_ok = r.status_code == 303
    print_test("Logout with revoked cookie", revoked_logout_ok, f"Status: {r.status_code}")
    
    results.append(("Logout edge cases", no_cookie_ok and revoked_logout_ok))
    
    # Cleanup S2
    s2.get(f'{ADMIN_URL}/logout')
    
    return results

# ============================================================================
# FEATURE 2: COMPLETE AUDIT FOR ENTITIES
# ============================================================================

def test_feature_2_audit_entities():
    print_section("FEATURE 2: COMPLETE AUDIT FOR ENTITIES")
    
    results = []
    
    # Setup: Get sync headers
    sync_headers = {
        'X-Sync-Key': SYNC_KEY,
        'X-Sync-Client': SYNC_CLIENT,
        'Content-Type': 'application/json'
    }
    
    # Step a: Create concesionari via sync API
    print("\n[Step a] Create concesionari via sync API...")
    
    conc_uid = str(uuid.uuid4())
    conc_op_id = str(uuid.uuid4())
    test_data['concesionari_uids'].append(conc_uid)
    
    payload = {
        "ops": [{
            "op_id": conc_op_id,
            "entity": "concesionari",
            "uid": conc_uid,
            "action": "upsert",
            "base_version": 0,
            "data": {
                "nume": "TEST-QA-Aud",
                "prenume": "A",
                "telefon": "0700"
            }
        }]
    }
    
    r = requests.post(f'{API_URL}/sync/entities/push', headers=sync_headers, json=payload)
    create_ok = r.status_code == 200 and r.json()['results'][0]['status'] == 'applied'
    print_test("Create concesionari", create_ok, f"Status: {r.status_code}, Result: {r.json()['results'][0]['status']}")
    
    if not create_ok:
        print("ERROR: Failed to create concesionari, aborting audit tests")
        return results
    
    # Verify audit_log entry
    conn = db_connect()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT * FROM audit_log 
        WHERE action = 'concesionari.create' 
        AND target = %s
        ORDER BY id DESC LIMIT 1
    """, (f'concesionari:{conc_uid}',))
    
    audit_row = cur.fetchone()
    if audit_row:
        audit_dict = dict(zip([desc[0] for desc in cur.description], audit_row))
        detail = json.loads(audit_dict['detail'])
        
        checks = [
            audit_dict['actor_type'] == 'sync',
            audit_dict['actor'] == SYNC_CLIENT,
            audit_dict['action'] == 'concesionari.create',
            audit_dict['target'] == f'concesionari:{conc_uid}',
            detail['old'] is None,
            detail['new']['nume'] == 'TEST-QA-Aud'
        ]
        
        print_test("Audit create checks", all(checks), 
                  f"actor_type={audit_dict['actor_type']}, actor={audit_dict['actor']}, old=None, new.nume={detail['new'].get('nume')}")
        results.append(("Create audit", all(checks)))
    else:
        print_test("Audit create entry", False, "No audit_log entry found")
        results.append(("Create audit", False))
    
    conn.close()
    
    # Step b: Update concesionari
    print("\n[Step b] Update concesionari via sync API...")
    
    update_op_id = str(uuid.uuid4())
    payload = {
        "ops": [{
            "op_id": update_op_id,
            "entity": "concesionari",
            "uid": conc_uid,
            "action": "upsert",
            "base_version": 1,
            "data": {
                "nume": "TEST-QA-Aud",
                "prenume": "A",
                "telefon": "0711"
            }
        }]
    }
    
    r = requests.post(f'{API_URL}/sync/entities/push', headers=sync_headers, json=payload)
    update_ok = r.status_code == 200 and r.json()['results'][0]['status'] == 'applied'
    print_test("Update concesionari", update_ok, f"Status: {r.status_code}")
    
    # Verify audit_log entry
    conn = db_connect()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT * FROM audit_log 
        WHERE action = 'concesionari.update' 
        AND target = %s
        ORDER BY id DESC LIMIT 1
    """, (f'concesionari:{conc_uid}',))
    
    audit_row = cur.fetchone()
    if audit_row:
        audit_dict = dict(zip([desc[0] for desc in cur.description], audit_row))
        detail = json.loads(audit_dict['detail'])
        
        checks = [
            audit_dict['action'] == 'concesionari.update',
            detail['old'].get('telefon') == '0700',
            detail['new'].get('telefon') == '0711',
            len(detail['old']) == 1,  # Only changed field
            len(detail['new']) == 1
        ]
        
        print_test("Audit update checks", all(checks),
                  f"old.telefon={detail['old'].get('telefon')}, new.telefon={detail['new'].get('telefon')}")
        results.append(("Update audit", all(checks)))
    else:
        print_test("Audit update entry", False, "No audit_log entry found")
        results.append(("Update audit", False))
    
    conn.close()
    
    # Step c: Replay same op_id (idempotent)
    print("\n[Step c] Replay same op_id (idempotent)...")
    
    r = requests.post(f'{API_URL}/sync/entities/push', headers=sync_headers, json=payload)
    replay_ok = r.status_code == 200 and r.json()['results'][0].get('duplicate') == True
    print_test("Idempotent replay", replay_ok, f"duplicate={r.json()['results'][0].get('duplicate')}")
    
    # Verify no additional audit entry
    conn = db_connect()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT COUNT(*) FROM audit_log 
        WHERE action = 'concesionari.update' 
        AND target = %s
    """, (f'concesionari:{conc_uid}',))
    
    update_count = cur.fetchone()[0]
    no_duplicate_audit = update_count == 1
    print_test("No duplicate audit", no_duplicate_audit, f"Update count: {update_count}")
    results.append(("Idempotent no duplicate audit", replay_ok and no_duplicate_audit))
    
    conn.close()
    
    # Push with no changes (new op_id, same data)
    no_change_op_id = str(uuid.uuid4())
    payload_no_change = {
        "ops": [{
            "op_id": no_change_op_id,
            "entity": "concesionari",
            "uid": conc_uid,
            "action": "upsert",
            "base_version": 2,
            "data": {
                "nume": "TEST-QA-Aud",
                "prenume": "A",
                "telefon": "0711"
            }
        }]
    }
    
    r = requests.post(f'{API_URL}/sync/entities/push', headers=sync_headers, json=payload_no_change)
    adopted_ok = r.status_code == 200 and r.json()['results'][0].get('adopted') == True
    print_test("No change adopted", adopted_ok, f"adopted={r.json()['results'][0].get('adopted')}")
    
    # Verify no audit entry for adopted
    conn = db_connect()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT COUNT(*) FROM audit_log 
        WHERE action = 'concesionari.update' 
        AND target = %s
    """, (f'concesionari:{conc_uid}',))
    
    still_one = cur.fetchone()[0] == 1
    print_test("No audit for adopted", still_one, "Update count still 1")
    
    conn.close()
    
    # Step d: Delete concesionari
    print("\n[Step d] Delete concesionari via sync API...")
    
    delete_op_id = str(uuid.uuid4())
    payload = {
        "ops": [{
            "op_id": delete_op_id,
            "entity": "concesionari",
            "uid": conc_uid,
            "action": "delete",
            "base_version": 2
        }]
    }
    
    r = requests.post(f'{API_URL}/sync/entities/push', headers=sync_headers, json=payload)
    delete_ok = r.status_code == 200 and r.json()['results'][0]['status'] == 'applied'
    print_test("Delete concesionari", delete_ok, f"Status: {r.status_code}")
    
    # Verify audit_log entry
    conn = db_connect()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT * FROM audit_log 
        WHERE action = 'concesionari.delete' 
        AND target = %s
        ORDER BY id DESC LIMIT 1
    """, (f'concesionari:{conc_uid}',))
    
    audit_row = cur.fetchone()
    if audit_row:
        audit_dict = dict(zip([desc[0] for desc in cur.description], audit_row))
        detail = json.loads(audit_dict['detail'])
        
        checks = [
            audit_dict['action'] == 'concesionari.delete',
            detail['new'] is None,
            detail['old'] is not None,
            'telefon' in detail['old']
        ]
        
        print_test("Audit delete checks", all(checks),
                  f"new=None, old has telefon={detail['old'].get('telefon')}")
        results.append(("Delete audit", all(checks)))
    else:
        print_test("Audit delete entry", False, "No audit_log entry found")
        results.append(("Delete audit", False))
    
    conn.close()
    
    # Step e: Create locuri_de_veci
    print("\n[Step e] Create locuri_de_veci via sync API...")
    
    loc_uid = str(uuid.uuid4())
    loc_op_id = str(uuid.uuid4())
    test_data['locuri_uids'].append(loc_uid)
    
    payload = {
        "ops": [{
            "op_id": loc_op_id,
            "entity": "locuri_de_veci",
            "uid": loc_uid,
            "action": "upsert",
            "base_version": 0,
            "data": {
                "cod_loc": "TEST-QA-LOC-1",
                "sector": "Q",
                "disponibilitate": "liber"
            }
        }]
    }
    
    r = requests.post(f'{API_URL}/sync/entities/push', headers=sync_headers, json=payload)
    loc_create_ok = r.status_code == 200 and r.json()['results'][0]['status'] == 'applied'
    print_test("Create locuri_de_veci", loc_create_ok, f"Status: {r.status_code}")
    
    # Verify audit
    conn = db_connect()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT COUNT(*) FROM audit_log 
        WHERE action = 'locuri_de_veci.create' 
        AND target = %s
    """, (f'locuri_de_veci:{loc_uid}',))
    
    loc_audit_ok = cur.fetchone()[0] == 1
    print_test("Audit locuri_de_veci.create", loc_audit_ok)
    results.append(("Locuri create audit", loc_create_ok and loc_audit_ok))
    
    conn.close()
    
    # Delete locuri_de_veci
    delete_loc_op_id = str(uuid.uuid4())
    payload = {
        "ops": [{
            "op_id": delete_loc_op_id,
            "entity": "locuri_de_veci",
            "uid": loc_uid,
            "action": "delete",
            "base_version": 1
        }]
    }
    
    r = requests.post(f'{API_URL}/sync/entities/push', headers=sync_headers, json=payload)
    loc_delete_ok = r.status_code == 200 and r.json()['results'][0]['status'] == 'applied'
    print_test("Delete locuri_de_veci", loc_delete_ok)
    
    # Verify audit
    conn = db_connect()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT COUNT(*) FROM audit_log 
        WHERE action = 'locuri_de_veci.delete' 
        AND target = %s
    """, (f'locuri_de_veci:{loc_uid}',))
    
    loc_del_audit_ok = cur.fetchone()[0] == 1
    print_test("Audit locuri_de_veci.delete", loc_del_audit_ok)
    results.append(("Locuri delete audit", loc_delete_ok and loc_del_audit_ok))
    
    conn.close()
    
    # Step f: Web admin path
    print("\n[Step f] Web admin path (staff user)...")
    
    # Login as staff
    s = requests.Session()
    r = s.post(f'{ADMIN_URL}/access/login', data={
        'source_id': 'cimitir',
        'username': STAFF_USER,
        'password': STAFF_PASSWORD
    }, allow_redirects=False)
    
    if r.status_code != 303:
        print_test("Staff login", False, f"Status: {r.status_code}")
        return results
    
    print_test("Staff login", True)
    
    # Create concesionar via web
    web_conc_data = {
        'nume': 'TEST-QA-Web',
        'prenume': 'W',
        'telefon': '0722'
    }
    
    r = s.post(f'{ADMIN_URL}/ui/concesionar',
              data=web_conc_data,
              headers={'Origin': 'http://127.0.0.1:8001', 'Sec-Fetch-Site': 'same-origin'},
              allow_redirects=False)
    
    web_create_ok = r.status_code == 303
    print_test("Web create concesionar", web_create_ok, f"Status: {r.status_code}")
    
    # Find the created concesionar in DB
    conn = db_connect()
    cur = conn.cursor()
    
    cur.execute("SELECT id, uid FROM concesionari WHERE nume = 'TEST-QA-Web' ORDER BY id DESC LIMIT 1")
    web_conc = cur.fetchone()
    
    if not web_conc:
        print_test("Find web concesionar", False, "Not found in DB")
        conn.close()
        return results
    
    web_conc_id, web_conc_uid = web_conc
    test_data['concesionari_uids'].append(web_conc_uid)
    print_test("Find web concesionar", True, f"ID: {web_conc_id}, UID: {web_conc_uid}")
    
    # Verify create audit
    cur.execute("""
        SELECT * FROM audit_log 
        WHERE action = 'concesionari.create' 
        AND target = %s
        ORDER BY id DESC LIMIT 1
    """, (f'concesionari:{web_conc_uid}',))
    
    web_audit = cur.fetchone()
    if web_audit:
        web_audit_dict = dict(zip([desc[0] for desc in cur.description], web_audit))
        web_checks = [
            web_audit_dict['actor_type'] == 'staff',
            web_audit_dict['actor'] == STAFF_USER
        ]
        print_test("Web create audit", all(web_checks),
                  f"actor_type={web_audit_dict['actor_type']}, actor={web_audit_dict['actor']}")
        results.append(("Web create audit", all(web_checks)))
    else:
        print_test("Web create audit", False, "No audit entry")
        results.append(("Web create audit", False))
    
    conn.close()
    
    # Update concesionar via web
    update_data = {
        'id': str(web_conc_id),
        'nume': 'TEST-QA-Web',
        'prenume': 'W2',
        'telefon': '0722'
    }
    
    r = s.post(f'{ADMIN_URL}/ui/concesionar',
              data=update_data,
              headers={'Origin': 'http://127.0.0.1:8001', 'Sec-Fetch-Site': 'same-origin'},
              allow_redirects=False)
    
    web_update_ok = r.status_code == 303
    print_test("Web update concesionar", web_update_ok, f"Status: {r.status_code}")
    
    # Verify update audit
    conn = db_connect()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT * FROM audit_log 
        WHERE action = 'concesionari.update' 
        AND target = %s
        ORDER BY id DESC LIMIT 1
    """, (f'concesionari:{web_conc_uid}',))
    
    web_update_audit = cur.fetchone()
    if web_update_audit:
        web_update_dict = dict(zip([desc[0] for desc in cur.description], web_update_audit))
        detail = json.loads(web_update_dict['detail'])
        
        web_update_checks = [
            web_update_dict['actor_type'] == 'staff',
            web_update_dict['actor'] == STAFF_USER,
            detail['old'].get('prenume') == 'W',
            detail['new'].get('prenume') == 'W2'
        ]
        print_test("Web update audit", all(web_update_checks),
                  f"old.prenume={detail['old'].get('prenume')}, new.prenume={detail['new'].get('prenume')}")
        results.append(("Web update audit", all(web_update_checks)))
    else:
        print_test("Web update audit", False, "No audit entry")
        results.append(("Web update audit", False))
    
    conn.close()
    
    # Delete concesionar via web
    r = s.post(f'{ADMIN_URL}/ui/concesionar/{web_conc_id}/delete',
              headers={'Origin': 'http://127.0.0.1:8001', 'Sec-Fetch-Site': 'same-origin'},
              allow_redirects=False)
    
    web_delete_ok = r.status_code == 303
    print_test("Web delete concesionar", web_delete_ok, f"Status: {r.status_code}")
    
    # Verify delete audit
    conn = db_connect()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT * FROM audit_log 
        WHERE action = 'concesionari.delete' 
        AND target = %s
        ORDER BY id DESC LIMIT 1
    """, (f'concesionari:{web_conc_uid}',))
    
    web_delete_audit = cur.fetchone()
    if web_delete_audit:
        web_delete_dict = dict(zip([desc[0] for desc in cur.description], web_delete_audit))
        web_delete_checks = [
            web_delete_dict['actor_type'] == 'staff',
            web_delete_dict['actor'] == STAFF_USER
        ]
        print_test("Web delete audit", all(web_delete_checks),
                  f"actor_type={web_delete_dict['actor_type']}, actor={web_delete_dict['actor']}")
        results.append(("Web delete audit", all(web_delete_checks)))
    else:
        print_test("Web delete audit", False, "No audit entry")
        results.append(("Web delete audit", False))
    
    conn.close()
    
    # Step g: Scan audit_log for secrets
    print("\n[Step g] Scan audit_log for secrets...")
    
    conn = db_connect()
    cur = conn.cursor()
    
    # Get all audit_log entries from this test run
    cur.execute("""
        SELECT ts, actor_type, actor, action, target, detail 
        FROM audit_log 
        WHERE ts > NOW() - INTERVAL '10 minutes'
    """)
    
    all_audit = cur.fetchall()
    secrets_to_check = [
        STAFF_PASSWORD,
        SYNC_KEY,
        'postgresql://',
        'sb_secret',
        'eyJ'  # JWT prefix
    ]
    
    secrets_found = []
    for row in all_audit:
        row_text = ' '.join(str(x) for x in row if x)
        for secret in secrets_to_check:
            if secret in row_text:
                secrets_found.append(f"Found '{secret[:10]}...' in audit_log")
    
    no_secrets = len(secrets_found) == 0
    print_test("No secrets in audit_log", no_secrets, 
              f"Checked {len(all_audit)} rows, found {len(secrets_found)} secrets")
    
    if secrets_found:
        for s in secrets_found[:3]:  # Show first 3
            print(f"  {s}")
    
    results.append(("No secrets in audit", no_secrets))
    
    conn.close()
    
    return results

# ============================================================================
# CLEANUP
# ============================================================================

def cleanup():
    print_section("CLEANUP")
    
    # Delete via sync API
    sync_headers = {
        'X-Sync-Key': SYNC_KEY,
        'X-Sync-Client': SYNC_CLIENT,
        'Content-Type': 'application/json'
    }
    
    # Delete concesionari
    for uid in test_data['concesionari_uids']:
        op_id = str(uuid.uuid4())
        payload = {
            "ops": [{
                "op_id": op_id,
                "entity": "concesionari",
                "uid": uid,
                "action": "delete",
                "base_version": 999  # High version to force delete
            }]
        }
        r = requests.post(f'{API_URL}/sync/entities/push', headers=sync_headers, json=payload)
        print(f"Delete concesionari {uid[:8]}: {r.status_code}")
    
    # Delete locuri_de_veci
    for uid in test_data['locuri_uids']:
        op_id = str(uuid.uuid4())
        payload = {
            "ops": [{
                "op_id": op_id,
                "entity": "locuri_de_veci",
                "uid": uid,
                "action": "delete",
                "base_version": 999
            }]
        }
        r = requests.post(f'{API_URL}/sync/entities/push', headers=sync_headers, json=payload)
        print(f"Delete locuri_de_veci {uid[:8]}: {r.status_code}")
    
    # Delete any remaining TEST-QA rows via SQL
    conn = db_connect()
    cur = conn.cursor()
    
    cur.execute("DELETE FROM concesionari WHERE nume LIKE 'TEST-QA-%'")
    print(f"Deleted {cur.rowcount} TEST-QA concesionari rows")
    
    cur.execute("DELETE FROM locuri_de_veci WHERE cod_loc LIKE 'TEST-QA-%'")
    print(f"Deleted {cur.rowcount} TEST-QA locuri rows")
    
    conn.commit()
    conn.close()
    
    print("\n✅ Cleanup complete")

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "="*80)
    print("  FEATURE VALIDATION TEST")
    print("  Testing secure logout and entity audit logging")
    print("="*80)
    
    all_results = []
    
    try:
        # Test Feature 1
        feature1_results = test_feature_1_secure_logout()
        all_results.extend(feature1_results)
        
        # Test Feature 2
        feature2_results = test_feature_2_audit_entities()
        all_results.extend(feature2_results)
        
    finally:
        # Always cleanup
        cleanup()
    
    # Summary
    print_section("SUMMARY")
    
    passed = sum(1 for _, ok in all_results if ok)
    total = len(all_results)
    
    print(f"\nTotal: {passed}/{total} tests passed")
    print("\nDetailed results:")
    for name, ok in all_results:
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status}: {name}")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED")
        return 0
    else:
        print(f"\n⚠️  {total - passed} TESTS FAILED")
        return 1

if __name__ == '__main__':
    sys.exit(main())
