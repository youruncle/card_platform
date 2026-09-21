import { cpSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const webDir = dirname(dirname(fileURLToPath(import.meta.url)));
const standaloneDir = join(webDir, ".next", "standalone");

if (!existsSync(join(standaloneDir, "server.js"))) {
  throw new Error(`Missing Next.js standalone build: ${standaloneDir}`);
}

function copyTree(source, destination) {
  if (!existsSync(source)) return;
  mkdirSync(destination, { recursive: true });
  cpSync(source, destination, { recursive: true, force: true });
}

copyTree(
  join(webDir, ".next", "static"),
  join(standaloneDir, ".next", "static"),
);
copyTree(join(webDir, "public"), join(standaloneDir, "public"));

console.log("Synced Next.js static and public assets into standalone output.");
