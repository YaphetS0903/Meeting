#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
backup_root="${1:-./backups}"
backup_run="$backup_root/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_run"
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}
# Stop writes before invoking this command for an application-consistent DB/file snapshot.
docker exec meeting-actions-postgres-1 pg_dump -U meetings -d meetings -Fc > "$backup_run/database.dump"
tar -h --exclude='local-admin-credentials.txt' --exclude='celerybeat-schedule*' -czf "$backup_run/storage.tar.gz" storage
database_hash="$(sha256_file "$backup_run/database.dump")"
storage_hash="$(sha256_file "$backup_run/storage.tar.gz")"
printf '{\n  "database.dump": "%s",\n  "storage.tar.gz": "%s"\n}\n' \
  "$database_hash" "$storage_hash" > "$backup_run/manifest.json"
if [ "$(id -u)" -eq 0 ]; then
  chown -R "${APP_UID:-10001}:${APP_GID:-10001}" "$backup_run"
fi
find "$backup_run" -type d -exec chmod 750 {} +
find "$backup_run" -type f -exec chmod 640 {} +
printf 'Backup saved: %s\n' "$backup_run"
