#!/usr/bin/env bash
# Puts the accounts' backup back where the accounts volume is empty: on a new server before
# its first `up`, or after the volume was lost. Run beside .env, with the backup's settings
# in it (env.example) and the stack down.
#
#   deploy/restore-accounts.sh
#
# Never over a database that's there: Litestream leaves one alone, and says so.
set -euo pipefail

(( $# == 0 )) || { printf 'usage: %s\n' "$0" >&2; exit 2; }
cd "$(dirname "$0")/.."

# A new volume takes its owner from the image that mounts it first: the app's, so Litestream,
# which runs as the app's user, can write the database it restores.
docker compose run --rm --no-deps app true
docker compose run --rm --no-deps backup \
  restore -if-db-not-exists -if-replica-exists /accounts/users.sqlite3
