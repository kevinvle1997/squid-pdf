// Made once, as the web image is built, at levels too slow for Caddy to spend its one core on per request.
import { readdir, readFile, writeFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { promisify } from "node:util";
import { brotliCompress, constants } from "node:zlib";

const compress = promisify(brotliCompress);
// Less for the far larger fonts, where the strongest takes many times as long; a woff2 is brotli already.
const LEVELS = { ".js": 11, ".css": 11, ".svg": 11, ".ttf": 9, ".otf": 9 };

const assets = join(process.argv[2] ?? "dist", "assets");
const names = (await readdir(assets)).filter((name) => extname(name) in LEVELS);
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
