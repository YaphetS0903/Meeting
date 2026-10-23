#!/usr/bin/env bash
# Read-only probe for an application reachable from outside the host.
# Usage: BASE_URL=http://mail.trqcns.com:4023 bash scripts/external-healthcheck.sh
set -euo pipefail

base_url="${BASE_URL:-http://mail.trqcns.com:4023}"
base_url="${base_url%/}"
verify_path="${WECOM_VERIFY_PATH:-/WW_verify_xdaVk9PpsUOtAD3F.txt}"
verify_expected="${WECOM_VERIFY_CONTENT:-xdaVk9PpsUOtAD3F}"
curl_args=(--fail --silent --show-error --location --max-time "${CURL_TIMEOUT_SECONDS:-10}")

check_status() {
  local url="$1"
  local expected_type="$2"
  local body_file status content_type
  body_file="$(mktemp)"
  trap 'rm -f "$body_file"' RETURN
  status="$(curl "${curl_args[@]}" --output "$body_file" --write-out '%{http_code}' "$url")"
  content_type="$(curl "${curl_args[@]}" --head --output /dev/null --write-out '%{content_type}' "$url" || true)"
  if [[ "$status" != "200" ]]; then
    echo "FAIL $url HTTP $status" >&2
    return 1
  fi
  if [[ -n "$expected_type" && "$content_type" != "$expected_type"* ]]; then
    echo "FAIL $url content-type=$content_type expected=$expected_type" >&2
    return 1
  fi
  printf 'OK   %s HTTP %s %s\n' "$url" "$status" "$content_type"
  cat "$body_file"
  printf '\n'
}

check_status "$base_url/" 'text/html'
health="$(curl "${curl_args[@]}" "$base_url/api/health")"
if [[ "$health" != *'"status":"ok"'* ]]; then
  echo "FAIL $base_url/api/health unexpected response" >&2
  exit 1
fi
printf 'OK   %s/api/health %s\n' "$base_url" "$health"
verify="$(curl "${curl_args[@]}" "$base_url$verify_path")"
if [[ "$verify" != "$verify_expected" ]]; then
  echo "FAIL $base_url$verify_path verification content mismatch" >&2
  exit 1
fi
printf 'OK   %s%s verification content matched\n' "$base_url" "$verify_path"
