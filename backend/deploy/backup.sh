#!/usr/bin/env bash
#
# Nightly Postgres backup to R2.
#
#   /srv/frame/backend/deploy/backup.sh
#   0 3 * * *  frame  /srv/frame/backend/deploy/backup.sh >> /var/log/frame-backup.log 2>&1
#
# What is worth being clear about: this backs up the DATABASE, not the
# photographs. The photographs live in R2, which already keeps its own copies,
# and re-uploading gigabytes nightly would cost more than it protects. What is
# irreplaceable here is small: which guest matched which photograph, what each
# studio bought, and who consented to what.
#
# A backup nobody has restored is a hypothesis. Restore one into a scratch
# database and count the rows before you rely on any of this.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/frame}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
FILE="${BACKUP_DIR}/frame-${STAMP}.dump"

# shellcheck disable=SC1091
set -a; source /srv/frame/backend/.env; set +a

# The connection string in .env is SQLAlchemy's dialect form. pg_dump wants a
# plain libpq URL, so strip the driver off.
PG_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"

mkdir -p "$BACKUP_DIR"

# -Fc is the custom format: compressed, and restorable table by table with
# pg_restore, which matters when what you actually need back is one studio's
# events rather than the whole cluster.
pg_dump --dbname="$PG_URL" --format=custom --no-owner --no-privileges --file="$FILE"

SIZE="$(stat -c%s "$FILE")"
if [ "$SIZE" -lt 20000 ]; then
    # A dump this small means it failed quietly, and a corrupt backup that
    # overwrites a good one is worse than no backup. Fail loudly instead.
    echo "backup suspiciously small (${SIZE} bytes), refusing to upload"
    exit 1
fi

gzip -9 "$FILE"
FILE="${FILE}.gz"

# Off the machine. A backup sitting on the server it is protecting is not a
# backup: it is deleted by whatever deletes the server.
if [ -n "${R2_BUCKET:-}" ]; then
    aws s3 cp "$FILE" "s3://${R2_BUCKET}/backups/$(basename "$FILE")" \
        --endpoint-url "https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" \
        --only-show-errors
    echo "uploaded $(basename "$FILE") ($(numfmt --to=iec "$(stat -c%s "$FILE")"))"
else
    echo "R2_BUCKET not set; backup kept locally only"
fi

find "$BACKUP_DIR" -name 'frame-*.dump.gz' -mtime "+${KEEP_DAYS}" -delete
echo "ok ${STAMP}"
