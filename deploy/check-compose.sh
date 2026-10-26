#!/usr/bin/env bash
# Proves the proxy image .env names was built beside this checkout's compose.yaml and
# env.example, so a deploy by tag alone never runs an image under files it wasn't checked
# with. Run beside .env, after `docker compose pull` and before `up` (compose.yaml).
#
#   deploy/check-compose.sh          checks the image
#   deploy/check-compose.sh --hash   prints the hash a build labels the image with
set -euo pipefail

cd "$(dirname "$0")/.."
label=squidpdf.compose-hash

# Each file is hashed under its name, so a line moved from one to the other still counts.
compose_hash() { sha256sum compose.yaml env.example | sha256sum | cut -d' ' -f1; }

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
# `with`, so a label missing from an image with no labels at all prints nothing, as an empty one does.
built_beside=$(docker image inspect "$image" --format "{{ with index .Config.Labels \"$label\" }}{{ . }}{{ end }}")
if [[ $built_beside == "$(compose_hash)" ]]; then
  printf 'ok    %s was built beside this compose.yaml and env.example\n' "$image"
  exit 0
fi
# This checkout's compose.yaml mounts no Caddyfile, so such a proxy would serve Caddy's own page.
if [[ -z $built_beside ]]; then
  fail "$image names no compose.yaml: it predates this check, or was built from source." \
    "Deploy it as its own commit's compose.yaml header says, checkout and all."
fi
if [[ $tag == latest ]]; then
  fail "$image was built beside another compose.yaml or env.example than these." \
    "Set SQUIDPDF_TAG in .env to a commit on main, then pull and run this again."
fi
fail "$image was built beside another compose.yaml or env.example than these." \
  "Move this checkout to its commit (git switch --detach $tag), add to .env what its" \
  "env.example adds, then pull and run this again."
