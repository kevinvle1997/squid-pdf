// Brotli copies beside the built files, which Caddy sends as they are (`precompressed`): made once, as the
// web image is built, at levels too slow for Caddy to spend its one core on for each request.
//   node scripts/compress.mjs dist
import { readdir, readFile, writeFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { promisify } from "node:util";
import { brotliCompress, constants } from "node:zlib";

const compress = promisify(brotliCompress);
// Brotli's level by kind: its strongest for what the page waits on, less for the far larger fonts the preview
// fetches once idle, where the strongest takes many times as long for a little more. A woff2 is brotli already.
const LEVELS = { ".js": 11, ".css": 11, ".svg": 11, ".ttf": 9, ".otf": 9 };

const assets = join(process.argv[2] ?? "dist", "assets");
const names = (await readdir(assets)).filter((name) => extname(name) in LEVELS);
// All at once: Node compresses on its pool of threads, a file a thread.
await Promise.all(
  names.map(async (name) => {
    const path = join(assets, name);
    const bytes = await readFile(path);
    const params = {
      [constants.BROTLI_PARAM_QUALITY]: LEVELS[extname(name)],
      [constants.BROTLI_PARAM_SIZE_HINT]: bytes.length,
    };
    await writeFile(`${path}.br`, await compress(bytes, { params }));
  }),
);
