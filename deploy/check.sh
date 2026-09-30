#!/usr/bin/env bash
# Proves a running stack serves through its gate: the gate refuses a stranger,
# then with the password the browser app, the health check, an upload, an export
# with one edit, compression, the security headers and a delete all work. Needs
# curl and jq.
#
#   deploy/check.sh https://your.domain user [root.crt]
#
# The password comes from SQUIDPDF_PASSWORD, or is asked for: never on the
# command line, where ps and the shell's history would keep it. root.crt is
# only for a local run, where Caddy signs its own certificate.
set -euo pipefail

usage() {
  printf 'usage: %s https://your.domain user [root.crt]
' "$0" >&2
  printf '  the password comes from SQUIDPDF_PASSWORD, or is asked for
' >&2
  exit 2
}
(( $# == 2 || $# == 3 )) || usage

base="$1"
user="$2"
ca="${3:-}"
password="${SQUIDPDF_PASSWORD:-}"
if [[ -z $password ]]; then
  read -rsp "password for $user: " password
  printf '\n' >&2
fi
pdf="$(dirname "$0")/../fixtures/sample.pdf"
work="$(mktemp -d)"
shown_bytes=300  # of a reply that failed, enough to read its Problem
trap 'rm -rf "$work"' EXIT

# The name and password go to curl in a file only this user reads, not as arguments.
escaped="${user}:${password}"
escaped="${escaped//\\/\\\\}"
escaped="${escaped//\"/\\\"}"
(umask 077 && printf 'user = "%s"\n' "$escaped" >"$work/login")

stranger=(--silent --show-error)
if [[ -n "$ca" ]]; then stranger+=(--cacert "$ca"); fi
# The owner cookie is how a document knows its browser, so it's kept between calls.
signed_in=("${stranger[@]}" --config "$work/login" --cookie-jar "$work/cookies" --cookie "$work/cookies")

fail() { printf 'FAIL  %s\n' "$*" >&2; exit 1; }
pass() { printf 'ok    %s\n' "$*"; }

# Prints "status seconds" for one request; the body goes to $work/body, headers to $work/headers.
call() { curl "$@" --output "$work/body" --dump-header "$work/headers" --write-out '%{http_code} %{time_total}\n'; }
# wc, not stat: stat's options differ between Linux and macOS.
size_of() { wc -c <"$1" | tr -d ' '; }
header() { grep -i "^$1:" "$work/headers" | cut -d: -f2- | tr -d ' \r' || true; }

# Retried at first, while a stack that has just started gets its certificate.
read -r status seconds < <(call "${stranger[@]}" --retry 10 --retry-all-errors --retry-delay 1 \
  "$base/api/health")
[[ $status == 401 ]] || fail "without the password, /api/health answered $status, not 401"
pass "the gate refuses a request without the password: $status in ${seconds}s"

read -r status seconds < <(call "${signed_in[@]}" "$base/api/health")
[[ $status == 200 ]] && jq -e '.status == "ok"' "$work/body" >/dev/null ||
  fail "with the password, /api/health answered $status: $(head -c "$shown_bytes" "$work/body")"
pass "/api/health answers through the gate: $status in ${seconds}s"
[[ $(header strict-transport-security) == max-age=31536000 ]] ||
  fail "the reply's Strict-Transport-Security is '$(header strict-transport-security)'"
[[ $(header x-content-type-options) == nosniff ]] ||
  fail "the reply's X-Content-Type-Options is '$(header x-content-type-options)'"
pass "the proxy sends HSTS and nosniff"

read -r status seconds < <(call "${signed_in[@]}" "$base/")
[[ $status == 200 ]] && grep -q '<div id="root">' "$work/body" ||
  fail "the browser app answered $status: $(head -c "$shown_bytes" "$work/body")"
pass "the browser app is served: $status in ${seconds}s"

read -r status seconds < <(call "${signed_in[@]}" --header 'content-type: application/pdf' \
  --data-binary @"$pdf" "$base/api/documents")
[[ $status == 201 ]] || fail "the upload answered $status: $(head -c "$shown_bytes" "$work/body")"
doc_id=$(jq -r .id "$work/body")
spans=$(jq '.spans | length' "$work/body")
edits=$(jq '{edits: [.spans[] | select(.text | startswith("Delivery"))
  | {kind: "replace", span_id: .id, text: (.text | sub("14 March"; "2 March"))}]}' "$work/body")
# Without its one edit, the export below would pass on the untouched file.
[[ $(jq '.edits | length' <<<"$edits") == 1 ]] || fail "the upload has no span to edit"
pass "an upload is read: $status in ${seconds}s, $spans spans"

read -r status seconds < <(call "${signed_in[@]}" --header 'accept-encoding: gzip' \
  "$base/api/documents/$doc_id")
[[ $status == 200 && $(header content-encoding) == gzip ]] ||
  fail "the document answered $status, content-encoding '$(header content-encoding)'"
pass "the proxy compresses the JSON: $(size_of "$work/body") bytes gzipped"

read -r status seconds < <(call "${signed_in[@]}" --header 'content-type: application/json' \
  --data "$edits" "$base/api/documents/$doc_id/export")
[[ $status == 200 && $(header content-type) == application/pdf ]] ||
  fail "the export answered $status: $(head -c "$shown_bytes" "$work/body")"
[[ $(head -c 5 "$work/body") == %PDF- ]] || fail "the export is not a PDF"
[[ -z $(header squid-skipped-edits) ]] || fail "the export left out edits $(header squid-skipped-edits)"
pass "an export with one edit downloads: $status in ${seconds}s, $(size_of "$work/body") bytes"

read -r status seconds < <(call "${signed_in[@]}" --request DELETE "$base/api/documents/$doc_id")
[[ $status == 204 ]] || fail "the delete answered $status"
pass "the document is deleted: $status"
