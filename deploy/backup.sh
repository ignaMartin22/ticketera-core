#!/usr/bin/env bash
# Backup diario de Postgres (pensado para cron). Guarda en /opt/ticketjuy/backups
# y borra los de más de 7 días.
set -euo pipefail

BACKUP_DIR=/opt/ticketjuy/backups
COMPOSE_FILE=/opt/ticketjuy/deploy/docker-compose.yml
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$BACKUP_DIR/ticketjuy-$STAMP.sql.gz"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Las variables POSTGRES_* ya están dentro del contenedor db.
docker compose -f "$COMPOSE_FILE" exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
chmod 600 "$OUT"

# Portadas de las fechas (volumen media del contenedor web).
MEDIA_OUT="$BACKUP_DIR/ticketjuy-media-$STAMP.tar.gz"
docker compose -f "$COMPOSE_FILE" exec -T web tar -C /app/media -czf - . > "$MEDIA_OUT.tmp"
mv "$MEDIA_OUT.tmp" "$MEDIA_OUT"
chmod 600 "$MEDIA_OUT"

find "$BACKUP_DIR" -name 'ticketjuy-*.sql.gz' -type f -mtime +7 -delete
find "$BACKUP_DIR" -name 'ticketjuy-media-*.tar.gz' -type f -mtime +7 -delete
echo "Backup OK: $OUT y $MEDIA_OUT"
