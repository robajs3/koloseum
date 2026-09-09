#!/bin/sh
set -e

# Czeka na dostępność Postgresa (przydatne przy docker-compose up,
# gdzie kontener bazy jeszcze się rozgrzewa).
if [ -n "$DB_HOST" ]; then
  DB_PORT="${DB_PORT:-5432}"
  echo "Czekam na Postgres pod ${DB_HOST}:${DB_PORT}..."
  i=0
  while ! python -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1)
try:
    s.connect(('${DB_HOST}', ${DB_PORT}))
except OSError:
    sys.exit(1)
" ; do
    i=$((i + 1))
    if [ "$i" -ge 30 ]; then
      echo "Postgres nie odpowiada po 30 próbach, przerywam."
      exit 1
    fi
    sleep 1
  done
  echo "Postgres gotowy."
fi

exec "$@"
