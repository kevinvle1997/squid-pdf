# Caddy serving the browser app, with the API behind it (Caddyfile, compose.yaml).
# Built from the repository's root, not react/: the preview draws in the very font
# files the server draws with, src/squidpdf/fonts.
FROM node:22-slim AS build
WORKDIR /repo/react
# Dependencies layer: only rebuilds when these change.
COPY react/package.json react/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/squidpdf/fonts/ /repo/src/squidpdf/fonts/
COPY react/ ./
# The commit this image is built from, whose code the landing links (compose.yaml passes it).
ARG SQUIDPDF_COMMIT
# Then each built file's brotli copy, which Caddy sends as it is (Caddyfile).
RUN npm run build && node scripts/compress.mjs dist

FROM caddy:2.11.4
# Where Caddy reads it by default. Its policy allows this build's style by hash, so the two ship as one.
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /repo/react/dist /srv
# The hash of the compose.yaml and env.example it was built beside, which a deploy checks (deploy/check-compose.sh).
ARG SQUIDPDF_COMPOSE_HASH
LABEL squidpdf.compose-hash="$SQUIDPDF_COMPOSE_HASH"
