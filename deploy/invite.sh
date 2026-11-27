#!/usr/bin/env bash
# Inviting someone again without --unlimited ends theirs, and clears a bounce.
set -euo pipefail

usage() {
  printf 'usage: %s [--unlimited] email [email ...]\n' "$0" >&2
  exit 2
}

cd "$(dirname "$0")/.."

unlimited=()
emails=()
for argument in "$@"; do
  if [[ $argument == --unlimited ]]; then
    unlimited=(--unlimited)
    continue
  fi
  [[ $argument == -* ]] && usage
  emails+=("$argument")
done
(( ${#emails[@]} > 0 )) || usage

for email in "${emails[@]}"; do
  # The +: an empty array is unset to bash before 4.4, which -u refuses.
  docker compose exec -T app squidpdf invite "$email" ${unlimited[@]+"${unlimited[@]}"}
done
