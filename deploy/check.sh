#!/usr/bin/env bash
# Proves a running stack serves what a stranger reaches: the browser app, the
# health check, the terms where the deployment names them, compression and the
# security headers. Then, where anyone may open documents, an upload, an export
# with one edit and a delete; where only the invited may, that a stranger's
# upload is refused. Needs curl and jq.
#
#   deploy/check.sh https://your.domain [root.crt]
#
# root.crt is only for a local run, where Caddy signs its own certificate.
set -euo pipefail

usage() {
  printf 'usage: %s https://your.domain [root.crt]\n' "$0" >&2
  exit 2
}
(( $# == 1 || $# == 2 )) || usage

base="$1"
ca="${2:-}"
pdf="$(dirname "$0")/../fixtures/sample.pdf"
work="$(mktemp -d)"
shown_bytes=300  # of a reply that failed, enough to read its Problem
trap 'rm -rf "$work"' EXIT

browser=(--silent --show-error)
if [[ -n "$ca" ]]; then browser+=(--cacert "$ca"); fi
# The owner cookie is how a document knows its browser, so it's kept between calls.
browser+=(--cookie-jar "$work/cookies" --cookie "$work/cookies")

fail() { printf 'FAIL  %s\n' "$*" >&2; exit 1; }
pass() { printf 'ok    %s\n' "$*"; }

# Prints "status seconds" for one request; the body goes to $work/body, headers to $work/headers.
call() { curl "$@" --output "$work/body" --dump-header "$work/headers" --write-out '%{http_code} %{time_total}\n'; }
# wc, not stat: stat's options differ between Linux and macOS.
size_of() { wc -c <"$1" | tr -d ' '; }
header() { grep -i "^$1:" "$work/headers" | cut -d: -f2- | tr -d ' \r' || true; }

# Retried at first, while a stack that has just started gets its certificate.
read -r status seconds < <(call "${browser[@]}" --retry 10 --retry-all-errors --retry-delay 1 \
  "$base/api/health")
[[ $status == 200 ]] && jq -e '.status == "ok"' "$work/body" >/dev/null ||
  fail "/api/health answered $status: $(head -c "$shown_bytes" "$work/body")"
pass "/api/health answers a stranger, with no password: $status in ${seconds}s"
# header() drops spaces, so the directive is matched without them.
[[ $(header strict-transport-security) == "max-age=31536000;includeSubDomains" ]] ||
  fail "the reply's Strict-Transport-Security is '$(header strict-transport-security)'"
[[ $(header x-content-type-options) == nosniff ]] ||
  fail "the reply's X-Content-Type-Options is '$(header x-content-type-options)'"
pass "the proxy sends HSTS and nosniff"

for docs in /api/docs /api/openapi.json; do
  read -r status seconds < <(call "${browser[@]}" "$base$docs")
  [[ $status == 404 ]] || fail "the API's $docs answered $status through the proxy, not 404"
done
pass "the API's docs aren't served through the proxy"

read -r status seconds < <(call "${browser[@]}" "$base/")
[[ $status == 200 ]] && grep -q '<div id="root">' "$work/body" ||
  fail "the browser app answered $status: $(head -c "$shown_bytes" "$work/body")"
pass "the browser app is served: $status in ${seconds}s"
# header() drops spaces, so the directive is matched without them.
[[ $(header content-security-policy) == *"frame-ancestors'none'"* ]] ||
  fail "the page's Content-Security-Policy is '$(header content-security-policy)'"
[[ -n $(header referrer-policy) && -n $(header permissions-policy) ]] ||
  fail "the page has no Referrer-Policy or Permissions-Policy"
pass "the page carries a Content-Security-Policy that refuses framing"
# Whether it's React Aria's own hash is react/src/policy.test.ts's to say; with none, its style is refused.
style_src=$(header content-security-policy | tr ';' '\n' | grep '^style-src' || true)
[[ $style_src == *"'sha256-"* ]] || fail "the page's policy allows no style by its hash: '$style_src'"
pass "the page's policy allows a style by its hash, as React Aria's needs"
[[ $(header cache-control) == no-cache ]] || fail "the page's Cache-Control is '$(header cache-control)'"
pass "the page is checked again on each visit"

script=$(grep -o '/assets/index-[^"]*\.js' "$work/body" | head -n 1)

read -r status seconds < <(call "${browser[@]}" "$base/api/terms")
[[ $status == 200 ]] || fail "/api/terms answered $status: $(head -c "$shown_bytes" "$work/body")"
if jq -e '. == null' "$work/body" >/dev/null; then
  pass "the deployment names no terms, so the landing links none"
else
  title=$(jq -er .title "$work/body") || fail "the terms have no title: $(head -c "$shown_bytes" "$work/body")"
  pass "the terms are served: $title, $(jq '.paragraphs | length' "$work/body") paragraphs"
  # The app's own page, which asks for them: under the same policy as the landing.
  read -r status seconds < <(call "${browser[@]}" "$base/terms")
  [[ $status == 200 ]] && grep -q '<div id="root">' "$work/body" ||
    fail "the terms' page answered $status: $(head -c "$shown_bytes" "$work/body")"
  [[ $(header content-security-policy) == *"default-src'none'"* ]] ||
    fail "the terms' page's Content-Security-Policy is '$(header content-security-policy)'"
  pass "the terms' page is the app's own, under its Content-Security-Policy"
fi
read -r status seconds < <(call "${browser[@]}" --header 'accept-encoding: br, gzip' "$base$script")
[[ $status == 200 && $(header content-encoding) == br ]] ||
  fail "the app's script answered $status, content-encoding '$(header content-encoding)'"
[[ $(header cache-control) == max-age=31536000,immutable ]] ||
  fail "the app's script's Cache-Control is '$(header cache-control)'"
pass "the app's script is sent as built, in brotli, and kept: $(size_of "$work/body") bytes"
read -r status seconds < <(call "${browser[@]}" "$base/assets/none.js")
[[ $status == 404 ]] || fail "a built file the build doesn't have answered $status, not 404"
[[ $(header cache-control) != *immutable* ]] || fail "the 404 is kept: '$(header cache-control)'"
pass "a built file the build doesn't have is missing, not the app's page, and not kept"

read -r status seconds < <(call "${browser[@]}" "$base/api/account")
[[ $status == 200 ]] || fail "/api/account answered $status: $(head -c "$shown_bytes" "$work/body")"
if jq -e '.invite_only' "$work/body" >/dev/null; then
  read -r status seconds < <(call "${browser[@]}" --header 'content-type: application/pdf' \
    --data-binary @"$pdf" "$base/api/documents")
  [[ $status == 401 ]] && jq -e '.type == "invited_only"' "$work/body" >/dev/null ||
    fail "invite-only, a stranger's upload answered $status: $(head -c "$shown_bytes" "$work/body")"
  pass "invite-only: a stranger's upload is refused, so the document steps are skipped"
  exit 0
fi
pass "anyone may open a document: $(jq -c . "$work/body")"

read -r status seconds < <(call "${browser[@]}" --header 'content-type: application/pdf' \
  --data-binary @"$pdf" "$base/api/documents")
[[ $status == 201 ]] || fail "the upload answered $status: $(head -c "$shown_bytes" "$work/body")"
doc_id=$(jq -r .id "$work/body")
spans=$(jq '.spans | length' "$work/body")
edits=$(jq '{edits: [.spans[] | select(.text | startswith("Delivery"))
  | {kind: "replace", span_id: .id, text: (.text | sub("14 March"; "2 March"))}]}' "$work/body")
# Without its one edit, the export below would pass on the untouched file.
[[ $(jq '.edits | length' <<<"$edits") == 1 ]] || fail "the upload has no span to edit"
pass "an upload is read: $status in ${seconds}s, $spans spans"

read -r status seconds < <(call "${browser[@]}" --header 'accept-encoding: gzip' \
  "$base/api/documents/$doc_id")
[[ $status == 200 && $(header content-encoding) == gzip ]] ||
  fail "the document answered $status, content-encoding '$(header content-encoding)'"
pass "the proxy compresses the JSON: $(size_of "$work/body") bytes gzipped"

read -r status seconds < <(call "${browser[@]}" --header 'content-type: application/json' \
  --data "$edits" "$base/api/documents/$doc_id/export")
[[ $status == 200 && $(header content-type) == application/pdf ]] ||
  fail "the export answered $status: $(head -c "$shown_bytes" "$work/body")"
[[ $(head -c 5 "$work/body") == %PDF- ]] || fail "the export is not a PDF"
[[ -z $(header squid-skipped-edits) ]] || fail "the export left out edits $(header squid-skipped-edits)"
pass "an export with one edit downloads: $status in ${seconds}s, $(size_of "$work/body") bytes"

read -r status seconds < <(call "${browser[@]}" --request DELETE "$base/api/documents/$doc_id")
[[ $status == 204 ]] || fail "the delete answered $status"
pass "the document is deleted: $status"
