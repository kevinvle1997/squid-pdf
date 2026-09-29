# squid-pdf

A web PDF editor that edits text in place

## Develop

The API, from the repository's root (needs [uv](https://docs.astral.sh/uv/)):

```
uv sync --all-extras
uv run uvicorn squidpdf.api.app:create_app --factory --port 8000
```

The browser app, in `react/` (needs Node 22). It sends `/api` on to the API:

```
cd react
npm ci
npm run dev          # http://localhost:5173
```

`npm test` runs the unit tests, `npm run e2e` the core loop in Chromium against a
real API it starts itself, and `npm run types` rewrites `src/api/schema.d.ts`
from the API's OpenAPI after the API changes.

## Deploy

The API runs behind [Caddy](https://caddyserver.com), which serves the
browser app beside it, gets the HTTPS certificate, compresses replies and asks
for a password on every page. On a
fresh server:

1. **Install Docker**, with its compose plugin:
   ```
   curl -fsSL https://get.docker.com | sh
   ```
2. **Point your domain at the server** (an A record, and AAAA for IPv6), and
   let in TCP ports 80 and 443, and UDP 443. Caddy needs port 80 to get the
   certificate.
3. **Get the code:**
   ```
   git clone https://github.com/kevinvle1997/squid-pdf.git
   cd squid-pdf
   ```
4. **Make your password's hash.** It asks for the password twice and prints
   the hash:
   ```
   docker run --rm -it caddy:2.11.4 caddy hash-password
   ```
5. **Fill in `.env`**: copy `env.example` to `.env` and set your domain, a user
   name and the hash, keeping the single quotes around it. `.env` stays on the
   server; git ignores it.
6. **Start it.** The first build takes a few minutes:
   ```
   docker compose up -d --build
   ```
7. **Check it**, from your own machine (needs curl and jq). It checks that the
   gate refuses a stranger, then uploads, exports and deletes a sample:
   ```
   read -rs password
   deploy/check.sh https://your.domain your-user-name "$password"
   ```
   Or open the domain in a browser: it asks for the name and password, and
   `/api/health` answers `{"status":"ok"}`.

To update: `git pull && docker compose up -d --build`. To read the logs:
`docker compose logs -f`. Documents are kept on the `documents` volume and
each is deleted after an hour untouched.

## Licence

[AGPL-3.0](LICENSE). Self-host it freely.
