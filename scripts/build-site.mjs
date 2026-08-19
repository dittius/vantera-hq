import { mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const publicDir = join(root, "public");
const dist = join(root, "dist");
rmSync(dist, { recursive: true, force: true });
mkdirSync(join(dist, "server"), { recursive: true });
mkdirSync(join(dist, ".openai"), { recursive: true });

const mime = {
  ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml",
};
const assets = {};
function collect(directory, prefix = "") {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    const absolute = join(directory, entry.name);
    if (entry.isDirectory()) collect(absolute, relative);
    else {
      const extension = entry.name.includes(".") ? `.${entry.name.split(".").pop()}` : "";
      assets[`/${relative}`] = { type: mime[extension] || "application/octet-stream", body: readFileSync(absolute).toString("base64") };
    }
  }
}
collect(publicDir);

const worker = `const ASSETS=${JSON.stringify(assets)};
function decode(value){const bytes=Uint8Array.from(atob(value),c=>c.charCodeAt(0));return bytes;}
export default {async fetch(request){const url=new URL(request.url);let path=url.pathname;if(path==="/")path="/index.html";const asset=ASSETS[path];if(!asset)return new Response("Not found",{status:404});const headers={"content-type":asset.type,"cache-control":path==="/data/state.json"?"no-store":"public, max-age=300","x-content-type-options":"nosniff","content-security-policy":"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'"};return new Response(decode(asset.body),{headers});}};`;
writeFileSync(join(dist, "server", "index.js"), worker);
writeFileSync(join(dist, ".openai", "hosting.json"), readFileSync(join(root, ".openai", "hosting.json")));
console.log(`Built VANTERA HQ Worker with ${Object.keys(assets).length} persisted assets.`);
