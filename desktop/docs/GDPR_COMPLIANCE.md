# 🛡️ Conformitate GDPR — Cimitir

**Regulamentul General privind Protecția Datelor** (Regulamentul UE 2016/679) se aplică oricărei aplicații care procesează date personale ale cetățenilor UE. Aplicația Cimitir stochează date sensibile (CNP-uri, adrese, date de deces) — deci **trebuie să fie GDPR-compliant**.

Acest document rezumă cum aplicația respectă GDPR și **ce mai trebuie să faci TU** (developer/distribuitor) pentru compliance completă.

---

## 1. Ce date personale procesează aplicația

| Categorie | Câmpuri | Sensibilitate |
|---|---|---|
| Concesionari | Nume, prenume, CNP, BI/CI, adresă, telefon, email | Ridicată (CNP = date sensibile) |
| Înhumați | Nume, prenume, CNP, dată naștere, deces, sex, cauză deces | Ridicată |
| Utilizatori aplicație | Username, parolă (hash) | Medie |
| Audit log | Cine ce a modificat | Medie |

Toate acestea sunt **date personale** în sensul GDPR.

---

## 2. Ce face aplicația AUTOMAT pentru GDPR

### ✅ Criptare în repaus (Art. 32)
- Baza de date locală este criptată cu **SQLCipher (AES-256)**.
- Cache-ul de licență e criptat cu AES-256-GCM.
- Audit log-ul e criptat cu AES-256-GCM.

### ✅ Autentificare securizată (Art. 32)
- Parolele stocate cu **PBKDF2-HMAC-SHA256** (200,000 iterații).
- Sesiuni cu roluri (admin/user) — accesul e restricționat.

### ✅ Jurnal de audit (Art. 30)
- Fiecare acțiune importantă (login, create/update/delete date) e logată.
- Loggul e criptat (nu poate fi citit de un utilizator curios).
- Se poate audita cine a modificat ce și când.

### ✅ Portabilitatea datelor (Art. 20)
- Utilizatorii pot exporta datele lor din aplicație (CSV/XLSX).
- Formatele sunt standard, ușor de portat la alt sistem.

### ✅ Ștergere sigură (Art. 17 „Right to erasure")
- Dezinstalarea prin Inno Setup lasă intact `%LOCALAPPDATA%\Cimitir\` — permite recuperare accidentală.
- Pentru ștergere DEFINITIVĂ: utilizatorul șterge manual `%LOCALAPPDATA%\Cimitir\` (documentat în manualul de utilizare).
- În aplicație există butoane de ștergere individuală pentru fiecare înregistrare.

---

## 3. Ce trebuie să FACI TU (developer/distribuitor)

### 📄 3.1 EULA / Politică de Confidențialitate

Aplicația afișează un EULA la instalare (via Inno Setup, fișier `LICENSE.txt`). Trebuie să adaugi în el o secțiune de confidențialitate care să menționeze:

1. **Cine ești tu** (developer / firmă): nume, adresă, CUI, contact.
2. **Ce date colectezi tu** de la client:
   - **Serverul de licențe** primește la activare: hardware_id (hash), device_name, IP-ul, app_version.
   - Nu primești: CNP-uri, nume concesionari, date medicale — acestea rămân LOCAL pe device-ul clientului.
3. **Scopul procesării** — activare licență, prevenire piraterie, statistici anonime.
4. **Perioada de retenție** — cât timp păstrezi datele pe server (recomand: cât timp licența e activă + 12 luni după revocare).
5. **Drepturile utilizatorului** — acces, rectificare, ștergere, portabilitate (Art. 15-22 GDPR).
6. **Contact DPO / firmă** — email pentru cereri GDPR.

**Recomand:** consultă un jurist specializat pentru redactarea finală. Sunt firme românești care oferă template-uri GDPR pentru software (ex: [avocatnet.ro](https://www.avocatnet.ro/), [privacypolicies.com](https://www.privacypolicies.com/)).

---

### 📝 3.2 DPA (Data Processing Agreement)

Când **vinzi** software-ul la o primărie/firmă, aceasta e „Data Controller" (deține datele cetățenilor), iar TU ești „Data Processor" (doar procesezi licența + eventual sync/backup cloud).

Semnează un **DPA** cu fiecare client B2B care specifică:
- Ce date procesezi (activare licență cu hardware_id).
- Ce garanții oferi (criptare, audit).
- Cine are acces la datele lor (nimeni, dacă e instalat local — nu ai acces la DB-ul clientului!).
- Procedura de notificare la breach (72h conform Art. 33 GDPR).

**Template gratuit:** [ANSPDCP](https://www.dataprotection.ro/) (Autoritatea Națională pentru Supravegherea Prelucrării Datelor cu Caracter Personal).

---

### 🔔 3.3 Notificare la breach (Art. 33)

Dacă ai un incident de securitate (ex: cineva sparge serverul de licențe), trebuie:
- **72 ore** — notifici ANSPDCP.
- Notifici toți clienții afectați.
- Documentezi incidentul (ce s-a întâmplat, ce date au fost afectate, ce măsuri ai luat).

**Recomandat:** ai un plan de răspuns la incident scris.

---

### 🔒 3.4 Securizare server licențe

Vezi și `DEPLOY_LICENSE_SERVER.md`:
- Firewall configurat (doar 443 deschis).
- HTTPS strict cu Let's Encrypt.
- MongoDB nu e accesibil public.
- Backup zilnic criptat.
- Update-uri OS regulate: `apt update && apt upgrade`.

---

### 📊 3.5 Statistici anonimizate DA, tracking PII NU

Dacă vrei să colectezi statistici (ex: câți utilizatori au aplicația, ce funcții folosesc), asigură-te că:
- **NU trimiți CNP-uri, nume, adrese** la server.
- **DA, poți trimite:** hardware_id (hash, nu identifică persoana), device_name (opțional), versiunea app, evenimente pseudo-anonime (ex: "user_opened_reports").

---

### 🗑 3.6 Ștergere la cerere

Utilizatorii clienților tăi (concesionarii, familiile înhumaților) au dreptul să-și ceară datele șterse (Art. 17). **Clientul tău (primăria)** e responsabilă să facă ștergerea în aplicație — dar aplicația trebuie să permită asta.

**Verifică:** aplicația permite:
- ✅ Ștergere concesionar
- ✅ Ștergere înhumat
- ✅ Ștergere plăți
- ⚠️ Ștergere audit log (dacă un utilizator cere) — TREBUIE implementată dacă nu există.

---

## 4. Checklist final GDPR pentru comercializare

- [ ] EULA cu politică de confidențialitate scrisă (revizuită de jurist).
- [ ] DPA template pregătit pentru B2B.
- [ ] Server licențe deploiat cu HTTPS + firewall.
- [ ] Backup automat pe serverul de licențe.
- [ ] Documentația clientului include instrucțiuni de:
  - Cum șterge datele unui concesionar.
  - Cum șterge complet aplicația + date.
  - Cum exportă datele pentru portabilitate.
- [ ] Plan de răspuns la incident documentat.
- [ ] Registru de procesare (Art. 30) — un tabel intern cu ce date procesezi, unde, pentru cât timp.
- [ ] (Opțional) Certificare ISO 27001 sau audit extern — cere pentru contracte cu instituții mari.

---

## 5. Resurse utile

- **ANSPDCP (autoritatea RO):** https://www.dataprotection.ro/
- **Textul GDPR:** https://eur-lex.europa.eu/eli/reg/2016/679/oj/rom
- **Template EULA + Privacy Policy pentru software:** https://www.privacypolicies.com/generator/
- **Consultanță juridică IT în RO:** avocati specializați în protecția datelor.

---

**Disclaimer:** Acest document e informativ, NU constituie consultanță juridică. Consultă un avocat specializat înainte de comercializare.
