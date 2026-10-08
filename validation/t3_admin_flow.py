"""Pas 3 - flux primul administrator (nivel HTTP, backend real pe 127.0.0.1:8001). Nu afiseaza parole."""
import requests
from common import get_secrets

B = "http://127.0.0.1:8001"
S = get_secrets()
U, TMP, NEW = S["user"], S["tmp_password"], S["new_password"]
res = []


def check(name, cond, extra=""):
    res.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def sess():
    return requests.Session()


def login(s, pw, user=U):
    return s.post(f"{B}/admin/access/login", data={"source_id": "cimitir", "username": user, "password": pw},
                  allow_redirects=False, timeout=30)


def loc(r):
    return r.headers.get("location", "")


# 1. parola gresita
s = sess()
r = login(s, "Gresit-123456!")
check("login parola gresita respins", r.status_code == 200 and "admin_token" not in "".join(s.cookies.keys()) and not s.cookies, f"status={r.status_code}")

# 2. parola temporara -> schimbare obligatorie
s = sess()
r = login(s, TMP)
check("login parola temporara -> redirect schimba-parola", r.status_code == 303 and "schimba-parola" in loc(r), loc(r))
cookie_hdr = r.headers.get("set-cookie", "")
print("   cookie flags:", "HttpOnly" in cookie_hdr, "SameSite" in cookie_hdr, "Secure" in cookie_hdr)

# 3. pagini admin blocate inainte de schimbare
for p in ("/admin/", "/admin/cloud", "/admin/system", "/admin/harta", "/admin/locuri", "/admin/setari", "/admin/monitorizare"):
    r = s.get(B + p, allow_redirects=False, timeout=30)
    check(f"blocat inainte de schimbare: {p}", r.status_code in (302, 303, 307) and "schimba-parola" in loc(r), f"{r.status_code} {loc(r)}")
# POST-uri admin blocate si ele
r = s.post(B + "/admin/ui/settings", data={}, allow_redirects=False, timeout=30)
check("POST /admin/ui/settings blocat inainte de schimbare", r.status_code in (302, 303, 307, 401, 403), str(r.status_code))
r = s.get(B + "/admin/export/locuri.xlsx", allow_redirects=False, timeout=30)
check("export xlsx blocat inainte de schimbare", r.status_code in (302, 303, 307, 401, 403), str(r.status_code))

# 4. validari schimbare parola
def chg(old, new, conf):
    return s.post(f"{B}/admin/schimba-parola", data={"old_password": old, "new_password": new, "confirm_password": conf},
                  allow_redirects=False, timeout=30)

r = chg(TMP, "abc", "abc"); check("parola slaba/scurta respinsa", r.status_code == 400, str(r.status_code))
r = chg(TMP, "Ab1!xyz", "Ab1!xyz"); check("parola slaba (7 caractere) respinsa", r.status_code == 400, str(r.status_code))
r = chg(TMP, NEW, NEW + "x"); check("confirmare gresita respinsa", r.status_code == 400, str(r.status_code))
r = chg(TMP, TMP, TMP); check("parola noua identica cu temporara respinsa", r.status_code == 400, str(r.status_code))
r = chg("Gresita-Curenta-1!", NEW, NEW); check("parola curenta gresita respinsa", r.status_code == 400, str(r.status_code))
r = s.get(B + "/admin/", allow_redirects=False, timeout=30)
check("inca blocat dupa incercari esuate", r.status_code in (302, 303, 307) and "schimba-parola" in loc(r), f"{r.status_code}")

# 5. schimbare corecta
r = chg(TMP, NEW, NEW)
check("schimbare corecta acceptata -> /admin/", r.status_code == 303 and loc(r).rstrip("/").endswith("/admin"), f"{r.status_code} {loc(r)}")
s.cookies.clear()
for k, v in r.cookies.items():
    s.cookies.set(k, v)
for p in ("/admin/", "/admin/cloud", "/admin/system"):
    r = s.get(B + p, allow_redirects=False, timeout=60)
    check(f"accesibil dupa schimbare: {p}", r.status_code == 200, str(r.status_code))
old_cookie = dict(s.cookies)

# 6. logout
r = s.post(B + "/admin/logout", headers={"Origin": B, "Sec-Fetch-Site": "same-origin"}, allow_redirects=False, timeout=30)
check("logout redirect /admin/access", r.status_code == 303 and "/admin/access" in loc(r), loc(r))
s2 = sess()
r = s2.get(B + "/admin/system", allow_redirects=False, timeout=30)
check("fara sesiune /admin/system protejat", r.status_code in (302, 303, 307) and "access" in loc(r), f"{r.status_code} {loc(r)}")
r = s2.get(B + "/admin/cloud", allow_redirects=False, timeout=30)
check("fara sesiune /admin/cloud protejat", r.status_code in (302, 303, 307) and "access" in loc(r), f"{r.status_code} {loc(r)}")

# 7. parola temporara nu mai merge, cea noua da
s3 = sess(); r = login(s3, TMP)
check("parola temporara nu mai functioneaza", r.status_code == 200 and not s3.cookies, str(r.status_code))
s4 = sess(); r = login(s4, NEW)
check("parola noua functioneaza direct (fara schimbare obligatorie)", r.status_code == 303 and "schimba-parola" not in loc(r), loc(r))

# 8. token vechi dupa logout (JWT stateless?)
s5 = sess()
for k, v in old_cookie.items():
    s5.cookies.set(k, v)
r = s5.get(B + "/admin/system", allow_redirects=False, timeout=60)
print("   INFO: token copiat inainte de logout, folosit dupa logout ->", r.status_code, "(200 = tokenul ramane valid dupa logout)")

print("\nREZUMAT:", sum(1 for _, ok in res if ok), "/", len(res), "PASS")
print("EȘUATE:", [n for n, ok in res if not ok])
