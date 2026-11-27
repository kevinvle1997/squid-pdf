#!/usr/bin/env bash
# Proves the proxy image was built beside this checkout's compose files and env.example.
set -euo pipefail

cd "$(dirname "$0")/.."
label=squidpdf.compose-hash

# Hashed under each name, so a line moved from one file to another still counts.
compose_hash() {
  sha256sum compose.yaml compose.backup.yaml env.example | sha256sum | cut -d' ' -f1
}

if [[ ${1:-} == --hash ]]; then
  compose_hash
  exit 0
fi
if (( $# > 0 )); then
  printf 'usage: %s [--hash]\n' "$0" >&2
  exit 2
fi

fail() {
  printf 'FAIL  %s\n' "$1" >&2
  shift
  printf '      %s\n' "$@" >&2
  exit 1
}

image=$(docker compose config --images | grep -- '-proxy:')
tag=${image##*:}
# `with`, so an image with no labels at all prints nothing, as an empty label does.
built_beside=$(docker image inspect "$image" --format "{{ with index .Config.Labels \"$label\" }}{{ . }}{{ end }}")
if [[ $built_beside == "$(compose_hash)" ]]; then
  printf 'ok    %s was built beside these compose files and env.example\n' "$image"
  exit 0
fi
if [[ -z $built_beside ]]; then
  fail "$image names no compose.yaml: it predates this check, or was built from source." \
    "Deploy it as its own commit's compose.yaml header says, checkout and all."
fi
if [[ $tag == latest ]]; then
  fail "$image was built beside other compose files or env.example than these." \
    "Set SQUIDPDF_TAG in .env to a commit on main, then pull and run this again."
fi
fail "$image was built beside other compose files or env.example than these." \
  "Move this checkout to its commit (git switch --detach $tag), add to .env what its" \
  "env.example adds, then pull and run this again."
