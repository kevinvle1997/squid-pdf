#!/usr/bin/env bash
# Lets people into a running stack's accounts, each address its own argument, as
# `squidpdf invite` does inside the app. Run beside .env, once the stack is up.
#
#   deploy/invite.sh them@example.com [more@example.com ...]
#   deploy/invite.sh --unlimited friend@example.com [...]   no document limit
#
# Inviting someone again without --unlimited ends theirs, and clears a bounce. Stops at the
# first address the app refuses; those before it are in, and inviting them again is harmless.
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
  # Anything else that looks like an option is a typo, not an address.
  [[ $argument == -* ]] && usage
  emails+=("$argument")
done
(( ${#emails[@]} > 0 )) || usage

for email in "${emails[@]}"; do
  # -T: no terminal is asked for, so it runs the same from a script or CI as by hand.
  # The +: an empty array is unset to bash before 4.4, which -u refuses.
  docker compose exec -T app squidpdf invite "$email" ${unlimited[@]+"${unlimited[@]}"}
done
