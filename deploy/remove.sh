#!/usr/bin/env bash
# An account whose month is paid is kept: cancel its subscription in Stripe first.
set -euo pipefail

usage() {
  printf 'usage: %s email [email ...]\n' "$0" >&2
  exit 2
}

cd "$(dirname "$0")/.."

(( $# > 0 )) || usage
for email in "$@"; do
  [[ $email == -* ]] && usage
done

for email in "$@"; do
  docker compose exec -T app squidpdf remove "$email"
done
