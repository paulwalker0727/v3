#!/usr/bin/env bash
# PostgreSQL LOCAL pentru dezvoltare/teste (NU pentru producție).
# Folosește un cluster separat în $CIMITIR_PG_DEV_DIR (implicit /app/.pgdata/main), port 55432, autentificare
# "trust" doar pe 127.0.0.1 (fără parolă în cod). Producția folosește DATABASE_URL către un Postgres gestionat.
#
#   scripts/dev_postgres.sh start|stop|status|url|reset
set -euo pipefail

PG_BIN="${PG_BIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
PG_DIR="${CIMITIR_PG_DEV_DIR:-/app/.pgdata/main}"
PG_PORT="${CIMITIR_PG_DEV_PORT:-55432}"
PG_DB="${CIMITIR_PG_DEV_DB:-cimitir_dev}"
PG_USER_NAME="${CIMITIR_PG_DEV_USER:-cimitir}"
RUN_AS=""
if [ "$(id -u)" = "0" ]; then RUN_AS="su postgres -s /bin/bash -c"; fi

run() { if [ -n "$RUN_AS" ]; then $RUN_AS "$*"; else bash -c "$*"; fi; }

url() { echo "postgresql://${PG_USER_NAME}@127.0.0.1:${PG_PORT}/${PG_DB}"; }

start() {
  if [ ! -f "$PG_DIR/PG_VERSION" ]; then
    mkdir -p "$PG_DIR"; [ -n "$RUN_AS" ] && chown -R postgres "$PG_DIR"
    run "$PG_BIN/initdb -D '$PG_DIR' -U postgres --auth=trust -E UTF8 --locale=C.UTF-8" >/dev/null
  fi
  if ! run "$PG_BIN/pg_ctl -D '$PG_DIR' status" >/dev/null 2>&1; then
    run "$PG_BIN/pg_ctl -D '$PG_DIR' -o '-p $PG_PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories=/tmp' -l '$PG_DIR/server.log' -w start" >/dev/null
  fi
  run "psql -h 127.0.0.1 -p $PG_PORT -U postgres -tAc \"SELECT 1 FROM pg_roles WHERE rolname='$PG_USER_NAME'\"" | grep -q 1 \
    || run "psql -h 127.0.0.1 -p $PG_PORT -U postgres -c \"CREATE ROLE $PG_USER_NAME LOGIN CREATEDB\"" >/dev/null
  run "psql -h 127.0.0.1 -p $PG_PORT -U postgres -tAc \"SELECT 1 FROM pg_database WHERE datname='$PG_DB'\"" | grep -q 1 \
    || run "psql -h 127.0.0.1 -p $PG_PORT -U postgres -c \"CREATE DATABASE $PG_DB OWNER $PG_USER_NAME\"" >/dev/null
  echo "PostgreSQL dev pornit. DATABASE_URL=$(url)"
}

case "${1:-}" in
  start) start ;;
  stop) run "$PG_BIN/pg_ctl -D '$PG_DIR' -m fast stop" ;;
  status) run "$PG_BIN/pg_ctl -D '$PG_DIR' status" ;;
  url) url ;;
  reset)
    run "psql -h 127.0.0.1 -p $PG_PORT -U postgres -c \"DROP DATABASE IF EXISTS $PG_DB WITH (FORCE)\"" >/dev/null
    run "psql -h 127.0.0.1 -p $PG_PORT -U postgres -c \"CREATE DATABASE $PG_DB OWNER $PG_USER_NAME\"" >/dev/null
    echo "Baza $PG_DB recreată (goală)." ;;
  *) echo "Utilizare: $0 start|stop|status|url|reset"; exit 1 ;;
esac
