import os

# Izolare de backend/.env (în pod rulează PostgreSQL): testele desktop folosesc implicit SQLite local.
os.environ.setdefault("CIMITIR_DB_ENGINE", "sqlite")
os.environ.setdefault("CIMITIR_MODE", "local")
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CIMITIR_ALLOW_DEFAULT_USERS", "1")
