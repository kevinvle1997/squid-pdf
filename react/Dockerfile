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
RUN npm run build

FROM caddy:2.11.4
COPY --from=build /repo/react/dist /srv
