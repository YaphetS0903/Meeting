#!/usr/bin/env bash
# Create an application-consistent backup and always restore previously running writers.
set -euo pipefail
umask 077

cd "$(dirname "$0")/.."
backup_root="$(readlink -f backups)"
mkdir -p "$backup_root"
staging_root="$backup_root/.staging"
mkdir -p -m 700 "$staging_root"
exec 9>"$backup_root/.scheduled-backup.lock"
if ! flock -n 9; then
  printf 'Another meeting-actions backup is already running\n'
  exit 0
fi

compose=(docker compose -f docker-compose.yml -f docker-compose.app.yml)
running_output="$("${compose[@]}" ps --services --status running)"
mapfile -t running_services <<< "$running_output"
stopped_services=()
for service in backend worker beat; do
  if printf '%s\n' "${running_services[@]}" | grep -qx "$service"; then
    stopped_services+=("$service")
  fi
done

restore_services() {
  status=$?
  trap - EXIT
  if ((${#stopped_services[@]})); then
    "${compose[@]}" start "${stopped_services[@]}" || status=1
  fi
  exit "$status"
}
trap restore_services EXIT

if ((${#stopped_services[@]})); then
  "${compose[@]}" stop "${stopped_services[@]}"
fi

output="$(bash scripts/backup.sh "$staging_root")"
backup_path="${output##*Backup saved: }"
case "$backup_path" in
  "$staging_root"/*) ;;
  *) printf 'Backup script did not return a path in the staging directory\n' >&2; exit 1 ;;
esac

manifest="$backup_path/manifest.json"
database_hash="$(sed -n 's/.*"database.dump": "\([0-9a-f]\{64\}\)".*/\1/p' "$manifest")"
storage_hash="$(sed -n 's/.*"storage.tar.gz": "\([0-9a-f]\{64\}\)".*/\1/p' "$manifest")"
test -n "$database_hash" && test -n "$storage_hash"
test "$(sha256sum "$backup_path/database.dump" | awk '{print $1}')" = "$database_hash"
test "$(sha256sum "$backup_path/storage.tar.gz" | awk '{print $1}')" = "$storage_hash"
docker exec -i meeting-actions-postgres-1 pg_restore -l < "$backup_path/database.dump" > /dev/null
tar -tzf "$backup_path/storage.tar.gz" > /dev/null
published_path="$backup_root/$(basename "$backup_path")"
test ! -e "$published_path"
mv -T "$backup_path" "$published_path"
printf 'Backup checks passed: %s\n' "$published_path"
