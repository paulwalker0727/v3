# Cimitir — MAIN (PySide6) + portal + backend comun

- Pornire locală, rute și reguli: [`LOCAL_SYNC.md`](LOCAL_SYNC.md)
- Raport Etapa 1 (audit și stabilizare): [`AUDIT_ETAPA1.md`](AUDIT_ETAPA1.md)
- Etapa 2 (personal, sincronizare, hartă publică): [`ETAPA2_SINCRONIZARE.md`](ETAPA2_SINCRONIZARE.md)
- Configurare: `.env.example` (fără secrete) → `.env`
- Teste: `cd backend && python -m pytest tests -q`
- Etapa 6 (desktop ONLINE + OFFLINE prin API, PostgreSQL = sursa principală): [`ETAPA6_ONLINE_OFFLINE.md`](ETAPA6_ONLINE_OFFLINE.md)
- Etapa 7 (documente online: storage S3-compatibil/Supabase, metadate în PostgreSQL): [`ETAPA7_DOCUMENTE_ONLINE.md`](ETAPA7_DOCUMENTE_ONLINE.md)
- Etapa 9 (scurtături Windows pentru administrator): [`tools/windows/README_ADMIN_WINDOWS.md`](tools/windows/README_ADMIN_WINDOWS.md)
- Phone Capture v1 (pagina „Trimite din teren” din portalul telefonului asociat): [`ETAPA_PHONE_CAPTURE.md`](ETAPA_PHONE_CAPTURE.md)
- Etapa 3 (registre locale multiple în MAIN: organizații → cimitire, izolare per registru, adoptarea instalației vechi): [`ETAPA3_REGISTRE_LOCALE.md`](ETAPA3_REGISTRE_LOCALE.md)

## Cloud deployment (Phone Capture + Field Inbox, fără MAIN)
Vezi [CLOUD_FIELD_DEPLOYMENT.md](CLOUD_FIELD_DEPLOYMENT.md) (ghid) și [CLOUD_FIELD_DEPLOYMENT_REPORT.md](CLOUD_FIELD_DEPLOYMENT_REPORT.md) (verificări). Șablon variabile: `.env.example`.
