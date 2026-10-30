# Instrucțiuni Windows — fix pairing Mobile și permisiuni

## 1. Aplicarea patchului (fără push / PR / merge automat)
```powershell
git clone https://github.com/paulwalker0727/v3.git ; cd v3
git checkout main ; git pull --ff-only origin main        # HEAD de bază verificat: 1a63c5c
git checkout -b fix/mobile-pairing-permissions
git am ..\CIMITIR_FIX_MOBILE_PAIRING_PERMISSIONS\fix-mobile-pairing-permissions.patch
# alternativ: git pull ..\CIMITIR_FIX_MOBILE_PAIRING_PERMISSIONS\fix-mobile-pairing-permissions.bundle fix/mobile-pairing-permissions
```
Nu se modifică licențierea `.lic`, Hardware ID, PROD, containerele 8086/8087.

## 2. Actualizarea backendului TEST
Urmează `ACTUALIZARE_BACKEND_TEST.md` (identitate → canary pe 8088 → verificare → switch cu `-Confirm` → rollback disponibil). MAIN nu își schimbă portul până nu s-a verificat identitatea.

## 3. Folosirea în MAIN
1. Centrul Cloud → F. Mobile → «Asociază telefoane (cod / QR)…» (fereastra „Telefoane Mobile”, fila **Asociere telefon**).
2. Bannerul de sus arată profilul Cloud activ, adresa, versiunea și identitatea bazei. Dacă backendul este vechi sau cheia MAIN e respinsă, vezi mesajul în română (nu se generează nimic).
3. «Generează cod pentru telefon» → cod + QR + timp rămas; «Copiază», «Revocă codul», «Reînnoiește codul».
4. Pe telefon: deschide aplicația Mobile și introdu codul (sau scanează QR). Telefonul apare în listă cu „Doar citire (implicit)”.
5. Selectează telefonul → «Acordă drepturi…» → fila **Permisiuni Mobile**: alege profilul (Administrator / Secretar / Personal cimitir / Doar consultare) și bifează Vede / Adaugă / Editează / Șterge pe fiecare categorie → «Salvează». Serverul impune drepturile.
6. Telefonul preia drepturile la deschiderea aplicației, la revenirea în prim-plan sau la sincronizare.
Varianta cu administrator Cloud (email + parolă) rămâne în Administrare → Cloud → Asociază → fila „Coduri și dispozitive”.

## 4. Rularea testelor (opțional)
Necesită PostgreSQL local de test (nu Supabase): `set CIMITIR_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:5432/postgres`, apoi
`python -m pytest backend/tests/test_mobile_pairing_permissions_e2e.py backend/tests/test_mobile_pairing_panel_unit.py -n 0` (UI PySide6 offscreen; PWA cere `playwright install chromium`).
