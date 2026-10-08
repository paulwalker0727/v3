#!/usr/bin/env python3
"""Test admin pages for SQL compatibility issues."""
import urllib.request
import urllib.parse
from http.cookiejar import CookieJar

BASE_URL = "http://127.0.0.1:8012"
cookie_jar = CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

def request(method, path, data=None):
    url = BASE_URL + path
    if data is not None:
        if isinstance(data, dict):
            data = urllib.parse.urlencode(data).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, method=method)
    try:
        resp = opener.open(req)
        return resp.status, resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8') if e.fp else ""
    except Exception as e:
        return None, str(e)

# Login first
print("Logging in...")
status, body = request("POST", "/admin/access/login", {
    "username": "admin",
    "password": "Parola-test-987",
    "source_id": "cimitir"
})
print(f"Login: {status}")

# Test admin pages
pages = [
    "/admin/",
    "/admin/locuri",
    "/admin/contracte",
    "/admin/plati",
    "/admin/inhumati",
    "/admin/cereri",
    "/admin/rapoarte",
    "/admin/setari",
    "/admin/utilizatori",
]

print("\nTesting admin pages:")
for page in pages:
    status, body = request("GET", page)
    if status == 200:
        print(f"✅ {page}: {status}")
    else:
        print(f"❌ {page}: {status}")
        if status == 500:
            print(f"   Error: {body[:200]}")
