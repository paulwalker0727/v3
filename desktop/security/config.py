"""
security/config.py — CONSTANTE DE COMPILARE

Aceste valori sunt embedded în EXE. La build sunt obfuscate cu PyArmor.
NU folosi variabile de mediu la runtime — ar putea fi modificate de utilizator.

Pentru dezvoltare, poți suprascrie temporar cu variabile de mediu setate în
sistemul tău. La build final, aceste variabile de mediu NU au efect.
"""
import os

# -----------------------------------------------------------------------------
# URL SERVER LICENȚE — schimbă înainte de build final pentru producție
# -----------------------------------------------------------------------------
LICENSE_SERVER_URL = os.environ.get(
    "CIMITIR_LICENSE_URL",
    "https://ai-play-8.preview.emergentagent.com"
)

# -----------------------------------------------------------------------------
# CHEIE PUBLICĂ RS256 — verificare offline a token-urilor de licență
# Aceasta este cheia publică. Cheia privată corespunzătoare este DOAR pe server.
# -----------------------------------------------------------------------------
LICENSE_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsOog8e8V04EhmIeKUaUz
ejxxPvH/itFRQAMTOCS0KSL2/sEaDgCSLxlhggJvJ2fV165xD3FZjFg0VkPkj4xC
VYOYPEOMJWoL+ouE0qlA7iZx2siqgMjsgwc33q5DX+cZFHE57DwjZ8rdoxvuXp0y
vYdK1p8MZzzpwHsXX6U8vmuhtsdM34mL758m4bn7W3dNG16DbwlsMmZ5mGZHMMdr
sLIdo40l5l1fpVCyMS03TqUMX6fo+CQ+pNPbprFxdMf22Ka5QRzkb63RUGa/BEJY
ON6cAMQpt+jRYJ71F64ZrXWcwnWsJKswhjqlsz8cytQwABBSEF0zSwEwJmhFNuvj
4wIDAQAB
-----END PUBLIC KEY-----
"""

# -----------------------------------------------------------------------------
# APLICAȚIE
# -----------------------------------------------------------------------------
APP_NAME = "Cimitir"
APP_VERSION = "1.0.0"
APP_ID_TAG = "com.cimitir.desktop"

# -----------------------------------------------------------------------------
# COMPORTAMENT LICENȚĂ
# -----------------------------------------------------------------------------
# Zile în care poți rula complet OFFLINE (fără re-validare online).
# Dacă token-ul local expiră și nu ai internet, aplicația trece în mod grace-period.
LICENSE_OFFLINE_GRACE_DAYS = 30

# Interval heartbeat (ore) — cât de des trimite ping serverului dacă are net
LICENSE_HEARTBEAT_HOURS = 24

# -----------------------------------------------------------------------------
# INTEGRITATE
# -----------------------------------------------------------------------------
# Dacă True → refuză să pornească dacă EXE-ul e modificat sau lipsesc fișiere critice.
ENFORCE_INTEGRITY = True

# Numele fișierelor critice pentru verificare. Se generează la build cu compute_hashes.py
INTEGRITY_MANIFEST = "integrity.json"
