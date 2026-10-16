# Cimitir — imagine cloud (Field Inbox + Phone Pairing + Phone Capture). Un singur proces: python -m cimitir_cloud_app
# Build din rădăcina repo-ului:  docker build -t cimitir-cloud .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/shared \
    PORT=8080

WORKDIR /app

COPY backend/requirements-cloud.txt backend/requirements-cloud.txt
RUN pip install -r backend/requirements-cloud.txt

COPY shared/ shared/
COPY backend/attachments.py backend/attachments.py
COPY backend/templates/phone_capture.html backend/templates/phone_capture.html
COPY backend/templates/mobile_app.html backend/templates/mobile_app.html
COPY backend/templates/partials/teren_capture.html backend/templates/partials/teren_capture.html
COPY backend/static/teren.js backend/static/teren_ui.js backend/static/teren.css backend/static/
COPY backend/static/mobile_app.js backend/static/mobile_app.css backend/static/mobile_manifest.webmanifest backend/static/mobile_sw.js backend/static/mobile-icon-192.png backend/static/mobile-icon-512.png backend/static/

RUN useradd --system --no-create-home --uid 10001 cimitir
USER 10001

EXPOSE 8080

# Liveness (fără DB/secrete). Readiness: GET /api/v1/health
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=4)"]

CMD ["python", "-m", "cimitir_cloud_app"]
