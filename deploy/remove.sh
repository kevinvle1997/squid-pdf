#!/usr/bin/env bash
# Takes people's accounts off a running stack, each address its own argument, as
# `squidpdf remove` does inside the app. Run beside .env, once the stack is up.
#
#   deploy/remove.sh them@example.com [more@example.com ...]
#
# Each is signed out of every browser at once, and nothing of theirs is kept. Where only
# the invited may sign in, they're out until invited again. Stops at the first address the
# app has no account for; those before it are gone.
set -euo pipefail

usage() {
  printf 'usage: %s email [email ...]\n' "$0" >&2
  exit 2
}

cd "$(dirname "$0")/.."

(( $# > 0 )) || usage
for email in "$@"; do
  # An option here is a typo, not an address.
  [[ $email == -* ]] && usage
done

for email in "$@"; do
  # -T: no terminal is asked for, so it runs the same from a script or CI as by hand.
  docker compose exec -T app squidpdf remove "$email"
done
