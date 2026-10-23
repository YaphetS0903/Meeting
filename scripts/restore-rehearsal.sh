#!/usr/bin/env bash
set -euo pipefail
backup_run="${1:?Usage: bash scripts/restore-rehearsal.sh backups/TIMESTAMP}"
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}
database_hash="$(sed -n 's/.*"database.dump": "\([0-9a-f]\{64\}\)".*/\1/p' "$backup_run/manifest.json")"
storage_hash="$(sed -n 's/.*"storage.tar.gz": "\([0-9a-f]\{64\}\)".*/\1/p' "$backup_run/manifest.json")"
test "${#database_hash}" -eq 64 && test "${#storage_hash}" -eq 64
test "$(sha256_file "$backup_run/database.dump")" = "$database_hash"
test "$(sha256_file "$backup_run/storage.tar.gz")" = "$storage_hash"
printf '%s: OK\n' "$backup_run/database.dump" "$backup_run/storage.tar.gz"
restore_db="rehearsal_$(date +%s)"
restore_files="$(mktemp -d /tmp/meeting-restore.XXXXXX)"
trap 'docker exec meeting-actions-postgres-1 dropdb -U meetings "$restore_db"; rm -rf "$restore_files"' EXIT
docker exec meeting-actions-postgres-1 createdb -U meetings "$restore_db"
docker exec -i meeting-actions-postgres-1 pg_restore -U meetings -d "$restore_db" --exit-on-error < "$backup_run/database.dump"
tar -xzf "$backup_run/storage.tar.gz" -C "$restore_files"
docker exec meeting-actions-postgres-1 psql -U meetings -d "$restore_db" -Atc 'SELECT count(*) FROM import_sources; SELECT count(*) FROM import_candidates;'
docker exec meeting-actions-postgres-1 psql -U meetings -d "$restore_db" -Atc "SELECT coalesce(json_agg(json_build_object('key', storage_key, 'sha256', sha256)), '[]'::json) FROM attachments;" > "$restore_files/attachments.json"
docker run --rm -i --user 0 -v "$restore_files:/restore:ro" meeting-actions-app:local python - /restore <<'PY'
import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1]);records=json.loads((p/'attachments.json').read_text())
for record in records:
    path=(p/'storage'/record['key']).resolve()
    assert path.is_relative_to((p/'storage').resolve()),'Invalid storage path'
    assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256'],'Attachment restore mismatch'
print(f'Attachment SHA-256 verified: {len(records)} files')
PY
printf 'Restore rehearsal passed in isolated database %s\n' "$restore_db"
