#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
spec="$(mktemp)"
trap 'rm -f "$spec"' EXIT
(cd .. && uv run python -c "import json, sys; from squidpdf.api.app import create_app; json.dump(create_app().openapi(), sys.stdout)") >"$spec"
# Fields with a default are optional to send, so a request needn't spell them out.
npx openapi-typescript "$spec" --default-non-nullable false --output src/api/schema.d.ts
