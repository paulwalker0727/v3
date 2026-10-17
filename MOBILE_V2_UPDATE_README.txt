CIMITIR MOBILE V2 — ETAPA 1

Acest pachet conține DOAR fișierele noi/modificate pentru branch-ul mobile-v2.
Se extrage peste rădăcina repository-ului v3-main, păstrând structura de directoare.
Nu conține .env, licenta.lic, baze locale, shared_data sau secrete.

După copiere:
  git status
  git add Dockerfile MOBILE_V2_PLAN.md MOBILE_V2_UPDATE_README.txt shared backend
  git commit -m "Add Mobile v2 read-only PWA foundation"

IMPORTANT: migrarea 020_mobile_v2_read.sql trebuie aplicată pe proiectul TEST înainte ca
căutarea/harta să poată citi registrele business cu rolul cimitir_cp_app.

Nu aplica pe producție. Proiectul actual are gardă explicită pentru ref-ul de producție.
