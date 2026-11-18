#!/usr/bin/env bash
# Shows what Stripe said of accounts on a running stack, each address its own argument, as
# `squidpdf billing` does inside the app. Run beside .env, once the stack is up.
#
#   deploy/billing.sh them@example.com [more@example.com ...]   paid until when, and each event
#   deploy/billing.sh --resync them@example.com [...]           asks Stripe again first
#
# --resync is for an event lost past Stripe's own tries: its answer is kept in the ledger.
# Stops at the first address the app has no account for, or can't ask Stripe about.
set -euo pipefail

usage() {
  printf 'usage: %s [--resync] email [email ...]\n' "$0" >&2
  exit 2
}

cd "$(dirname "$0")/.."

resync=()
emails=()
for argument in "$@"; do
  if [[ $argument == --resync ]]; then
    resync=(--resync)
    continue
  fi
  # Anything else that looks like an option is a typo, not an address.
  [[ $argument == -* ]] && usage
  emails+=("$argument")
done
(( ${#emails[@]} > 0 )) || usage

for email in "${emails[@]}"; do
  # -T: no terminal is asked for, so it runs the same from a script or CI as by hand.
  # The +: an empty array is unset to bash before 4.4, which -u refuses.
  docker compose exec -T app squidpdf billing "$email" ${resync[@]+"${resync[@]}"}
done
