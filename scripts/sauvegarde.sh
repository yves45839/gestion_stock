#!/usr/bin/env bash
# Sauvegarde quotidienne : export PostgreSQL compressé + copie du dossier media.
# Usage : scripts/sauvegarde.sh   (prévu pour cron, voir DEPLOIEMENT.md)
set -euo pipefail

cd "$(dirname "$0")/.."

DEST="${SAUVEGARDE_DIR:-$HOME/sauvegardes}"
JOURS="${SAUVEGARDE_JOURS:-14}"
HORODATAGE="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$DEST/base" "$DEST/media"

# 1. Base de données : on écrit dans un fichier temporaire, vérifié avant d'être gardé.
TMP="$DEST/base/.en-cours.sql.gz"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > "$TMP"
gzip -t "$TMP"
if [ "$(stat -c %s "$TMP")" -lt 10000 ]; then
    echo "$(date '+%F %T') ECHEC : export de la base anormalement petit" >&2
    exit 1
fi
mv "$TMP" "$DEST/base/base-$HORODATAGE.sql.gz"

# 2. Fichiers media : copie miroir (les fichiers supprimés dans l'application sont conservés ici).
docker compose cp web:/app/media/. "$DEST/media/" > /dev/null

# 3. Ménage : on garde les exports des $JOURS derniers jours.
find "$DEST/base" -name 'base-*.sql.gz' -mtime +"$JOURS" -delete

echo "$(date '+%F %T') OK : base-$HORODATAGE.sql.gz ($(du -h "$DEST/base/base-$HORODATAGE.sql.gz" | cut -f1)), media $(find "$DEST/media" -type f | wc -l) fichiers"
