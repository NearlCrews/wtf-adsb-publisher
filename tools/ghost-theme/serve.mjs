import http from "node:http";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { preview } from "./paths.mjs";

const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".png": "image/png",
  ".ico": "image/vnd.microsoft.icon",
  ".xml": "application/xml; charset=utf-8",
  ".json": "application/json",
  ".ttf": "font/ttf",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
};

export async function startServer(port = 0, directory = preview) {
  // Requested files are compared by real path, so resolve the root the same way.
  const root = await fs.realpath(directory);
  // Like the deployed Caddy file server, a directory serves index.html, then
  // index.txt; like the public proxy, /rss/ serves the feed's index.xml.
  async function resolveFile(pathname) {
    const target = path.resolve(root, `.${decodeURIComponent(pathname)}`);
    if (!pathname.endsWith("/")) return fs.realpath(target);
    if (pathname === "/rss/")
      return fs.realpath(path.join(target, "index.xml"));
    return fs
      .realpath(path.join(target, "index.html"))
      .catch(() => fs.realpath(path.join(target, "index.txt")));
  }
  const server = http.createServer(async (request, response) => {
    try {
      if (!["GET", "HEAD"].includes(request.method)) {
        response.writeHead(405);
        response.end();
        return;
      }
      const url = new URL(request.url, "http://localhost");
      const real = await resolveFile(url.pathname);
      if (!real.startsWith(`${root}${path.sep}`)) {
        response.writeHead(403);
        response.end();
        return;
      }
      const body = await fs.readFile(real);
      response.writeHead(200, {
        "Content-Type":
          url.pathname === "/rss/"
            ? "application/rss+xml; charset=utf-8"
            : types[path.extname(real)] || "application/octet-stream",
        "X-Content-Type-Options": "nosniff",
        "X-Robots-Tag": "noindex, nofollow",
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
      });
      response.end(request.method === "HEAD" ? undefined : body);
    } catch {
      response.writeHead(404, { "Content-Type": "text/plain" });
      response.end("Not found");
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
  return server;
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const server = await startServer(Number(process.env.PREVIEW_PORT || "8788"));
  console.log(`Preview: http://127.0.0.1:${server.address().port}/`);
}
