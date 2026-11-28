#!/bin/sh
set -e

# Applique les migrations au démarrage du service web uniquement
# (le worker Celery définit RUN_MIGRATIONS=false).
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    python manage.py migrate --noinput
fi

exec "$@"
