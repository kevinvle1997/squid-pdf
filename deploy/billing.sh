#!/usr/bin/env bash
# --resync asks Stripe again, for an event lost past Stripe's own tries.
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
  [[ $argument == -* ]] && usage
  emails+=("$argument")
done
(( ${#emails[@]} > 0 )) || usage

for email in "${emails[@]}"; do
  # The +: an empty array is unset to bash before 4.4, which -u refuses.
  docker compose exec -T app squidpdf billing "$email" ${resync[@]+"${resync[@]}"}
done
