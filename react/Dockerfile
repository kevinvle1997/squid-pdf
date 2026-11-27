# Built from the repository's root: the preview draws in the server's own fonts, src/squidpdf/fonts.
FROM node:22-slim AS build
WORKDIR /repo/react
COPY react/package.json react/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/squidpdf/fonts/ /repo/src/squidpdf/fonts/
COPY react/ ./
ARG SQUIDPDF_COMMIT
RUN npm run build && node scripts/compress.mjs dist

FROM caddy:2.11.4
# Its policy allows this build's style by hash, so the two ship as one.
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /repo/react/dist /srv
ARG SQUIDPDF_COMPOSE_HASH
LABEL squidpdf.compose-hash="$SQUIDPDF_COMPOSE_HASH"
