# 🔐 Arhitectura de Securitate — Cimitir

**Scop:** protejarea codului sursă, a datelor clienților și a modelului de business (licențiere) împotriva pirateriei și accesului neautorizat.

---

## 1. Straturile de securitate (Defense in Depth)

```
┌──────────────────────────────────────────────────────┐
│  1. INTEGRITATE EXE       (SHA-256 manifest)        │
├──────────────────────────────────────────────────────┤
│  2. OBFUSCARE COD         (PyArmor Community)       │
├──────────────────────────────────────────────────────┤
│  3. LICENȚIERE            (JWT RS256 + Hardware ID) │
├──────────────────────────────────────────────────────┤
│  4. CRIPTARE DB LOCALĂ    (SQLCipher AES-256)       │
├──────────────────────────────────────────────────────┤
│  5. CRIPTARE CACHE        (AES-256-GCM per device)  │
├──────────────────────────────────────────────────────┤
│  6. HASH PAROLE           (PBKDF2-HMAC-SHA256 200k) │
├──────────────────────────────────────────────────────┤
│  7. AUDIT LOG CRIPTAT     (AES-256, rotire zilnică) │
└──────────────────────────────────────────────────────┘
```

Fiecare strat e independent. Un atacator trebuie să spargă TOATE ca să pirateze aplicația.

---

## 2. Cum funcționează licențierea

### 2.1 Cheia publică / privată RS256

- Serverul are **cheia privată** (`LICENSE_PRIVATE_KEY_B64` în `.env`) — folosită pentru a **semna** token-urile de licență.
- Clientul are **cheia publică** (`security/config.py::LICENSE_PUBLIC_KEY_PEM`) — folosită pentru a **verifica** token-urile **offline**.

Un atacator care are EXE-ul găsește cheia publică — dar cu ea NU poate semna token-uri false. Doar cheia privată (de pe serverul tău) poate face asta.

### 2.2 Flow-ul de activare

```
Client                           Server (VPS)
  |                                    |
  |  1. User introduce cheia CIM-...   |
  |                                    |
  |  2. Compute hardware_id (SHA-256)  |
  |     din MAC + CPU + Disk + MachineID|
  |                                    |
  |  3. POST /api/public/activate ────>|
  |     {license_key, hardware_id,     |
  |      device_name, app_version}     |
  |                                    |
  |                                4. Verifică:
  |                                   - există cheia?
  |                                   - nu e revocată?
  |                                   - nu a expirat?
  |                                   - max_devices nedepășit?
  |                                   ✅ Salvează activation
  |                                    |
  |  <──── 5. license_token (JWT RS256)|
  |         semnat cu cheia privată    |
  |                                    |
  |  6. Verifică token cu cheia publică|
  |     (offline, cu jwt.decode)       |
  |                                    |
  |  7. Salvează în cache local        |
  |     criptat AES-256-GCM cu cheia   |
  |     derivată din hardware_id       |
  |     Locație: %LOCALAPPDATA%\Cimitir\license.dat
```

### 2.3 Flow-ul la pornire (după activare)

```
Client
  |
  |  1. Citește cache-ul din license.dat
  |     (dacă lipsește → arată fereastra de activare)
  |
  |  2. Decriptează cu hardware_id curent
  |     (dacă fișierul a fost mutat pe alt PC → InvalidTag → cache invalidat)
  |
  |  3. Verifică JWT-ul OFFLINE cu cheia publică:
  |     - semnătură validă?
  |     - hardware_id în token == hardware_id curent?
  |     - exp > now?
  |
  |  4. Dacă are internet → POST /api/public/validate
  |     (verificare rapidă că licența nu a fost revocată între timp)
  |
  |  5. Dacă totul OK → deschide aplicația
```

### 2.4 Cache offline + grace period

Token-ul are valabilitate **30 zile**. Aplicația funcționează complet offline pe această perioadă. La fiecare pornire online, tokenul se re-emite (extinde valabilitatea).

Dacă clientul nu are net timp de 30+ zile → aplicația refuză să pornească până se conectează.

**Beneficii:**
- Nu depinzi de uptime-ul VPS-ului pentru clienți activi.
- Poți revoca licențele — la primul contact online, cache-ul e invalidat.
- Transferul licenței între PC-uri = revocare activare veche + activare nouă.

---

## 3. Ce face fiecare strat

### Strat 1 — Integritate EXE

**Modul:** `security/integrity.py`

- La build: `compute_hashes.py` generează `integrity.json` cu SHA-256 pentru fiecare `.exe`, `.dll`, `.pyd` din `dist/Cimitir/`.
- La runtime: `check_integrity()` verifică fiecare fișier. Dacă unul e modificat → refuză pornirea.

**Ce protejează:**
- Utilizatori care patchează EXE-ul (ex. să treacă peste licență).
- Malware care injectează cod în aplicație.

**Limitare:** un atacator sofisticat poate regenera și `integrity.json`. Combină cu semnarea digitală a EXE-ului (secțiunea 8).

### Strat 2 — Obfuscare cod

**Unealtă:** [PyArmor Community](https://pyarmor.readthedocs.io/) (gratuit, open-source, fără telemetrie, GDPR-compliant).

- Face bytecode-ul non-trivial de decompilat.
- Nu trimite date la niciun server extern.

**Comandă (integrată în `build.bat`):**
```
python -m pyarmor gen --output=obfuscated --recursive .
```

**Ce protejează:**
- Concurenți care încearcă să învețe cum funcționează codul tău.
- Utilizatori care încearcă să scoată verificarea de licență.

**Limitare:** obfuscarea nu e criptare. Un reverse-engineer expert va putea totuși să înțeleagă codul cu timp/instrumente. Combină cu integritate + licențiere online.

### Strat 3 — Licențiere (vezi 2.x)

### Strat 4 — Criptare DB locală (SQLCipher)

**Modul:** `security/db_crypto.py`

- Folosește **[SQLCipher](https://www.zetetic.net/sqlcipher/)** — extensie SQLite cu AES-256 nativ (open-source, industry-standard).
- Cheia DB e derivată **la runtime** din `hardware_id + license_key` (nu se stochează niciodată).
- Dacă cineva copiază `cimitir.db` pe alt PC → nu-l poate deschide (cheia diferă).

**Migrare din DB necriptat:**
- La primul start cu SQLCipher, dacă există `cimitir.db` necriptat → migrează automat la `cimitir.encrypted.db` și șterge originalul (după backup).

**Ce protejează:**
- Furt fizic al calculatorului → datele personale ale clienților rămân protejate.
- GDPR compliance — datele sensibile (CNP-uri, adrese) sunt criptate în repaus.

### Strat 5 — Cache criptat pentru licență

**Modul:** `security/settings_store.py` + `crypto_utils.py`

- Fișier: `%LOCALAPPDATA%\Cimitir\license.dat`
- Criptat cu **AES-256-GCM**, cheie derivată din `hardware_id` cu PBKDF2 (200k iterații).
- Include token-ul JWT + info licență + timestamp.
- Dacă e mutat pe alt PC → decriptarea eșuează → cache invalidat automat.

### Strat 6 — Hash parole utilizatori

**Modul:** `security/password_kdf.py`

- **PBKDF2-HMAC-SHA256, 200,000 iterații**, salt de 16 bytes per parolă.
- Format: `pbkdf2$<iter>$<salt_hex>$<hash_hex>`.
- Backward compatible cu hash-urile legacy SHA-256 din versiunile vechi.
- La login cu hash legacy → auto-upgrade transparent la PBKDF2.

**Ce protejează:**
- Dacă cineva fură DB-ul → nu poate brute-force parolele ușor (200k iterații × miliarde încercări ⇒ ani întregi).

### Strat 7 — Audit log criptat

**Modul:** `security/audit_log.py`

- Fișier zilnic în `%LOCALAPPDATA%\Cimitir\audit\audit-YYYYMMDD.log.enc`.
- Fiecare linie criptată cu AES-256-GCM.
- Loghează: login, logout, create/update/delete entități, activare licență, integritate eșuată, etc.
- Poate fi decriptat DOAR pe device-ul care l-a creat (cheia = hardware_id).

**Ce protejează / oferă:**
- Trasabilitate pentru instituții (primării, GDPR).
- Investigație în caz de abuz sau incident.
- Confidențialitatea logurilor față de utilizatori curioși.

---

## 4. Ce NU protejează sistemul (limitări)

1. **Cracking manual sofisticat** — un reverse-engineer expert cu IDA Pro poate spune eventual "aici e verificarea de licență". Combină cu semnarea digitală + heuristici la runtime (verificare periodică online) pentru descurajare maximă.

2. **Interceptarea traficului către server** — dacă atacatorul controlează DNS-ul, poate direcționa `cimitir.ro` la un server fals. **Soluție:** certificate pinning (poți adăuga în viitor).

3. **Screen capture / OCR** — dacă cineva vede datele pe ecran, nu ai cum să oprești. Pentru date foarte sensibile, apelează la watermark cu numele operatorului.

---

## 5. Recomandări pentru maximă securitate

1. ✅ **Semnare digitală** EXE cu certificat OV Code Signing (~$100/an de la [Certum](https://shop.certum.eu/) sau [Sectigo](https://sectigo.com/)). Elimină ~90% din false-positives AV.

2. ✅ **HTTPS strict** pe VPS-ul de licențe cu **Let's Encrypt** (gratuit, auto-renewal).

3. ✅ **Firewall pe VPS** — deschide DOAR portul 443 spre exterior (blochează 80, 22 dacă nu ai nevoie de SSH din LAN).

4. ✅ **Backup DB serverului** zilnic (script cron mongodump).

5. ✅ **Monitorizare activări suspecte** — alertă dacă o licență activează pe > 3 device-uri diferite într-o săptămână.

6. ✅ **2FA pentru admin dashboard** (recomandare pentru versiuni viitoare).

---

## 6. Conformitate GDPR

Aplicația se poate declara **compliant GDPR** dacă respecti:

- ✅ Datele personale (CNP, adresă înhumat/concesionar) sunt **criptate în repaus** (SQLCipher). ✔️
- ✅ Există **jurnal de audit** al accesului la date. ✔️
- ✅ Există **procedură de ștergere** (dezinstalare + ștergere `%LOCALAPPDATA%\Cimitir\`). ✔️
- ⚠️ **Politica de confidențialitate** — trebuie scrisă și afișată la instalare (EULA). Consultă un jurist.
- ⚠️ **DPA (Data Processing Agreement)** cu clientul (primărie/firmă) — obligatoriu B2B.
- ⚠️ **Ștergere audit log la cerere** — implementează un buton "Șterge logurile mai vechi de X luni".

Vezi `GDPR_COMPLIANCE.md` pentru detalii.
