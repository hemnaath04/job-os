/**
 * Bundle the extension into dist/.
 *
 * esbuild rather than Vite: the repo has no Vite anywhere, the content script
 * has to be a plain IIFE with no module loader (Chrome injects it as a classic
 * script into a page it does not control), and the service worker needs to be a
 * separate ESM bundle. That is three one-line esbuild calls against a much
 * smaller dependency surface.
 *
 * Nothing here fetches or generates code at runtime. Everything the extension
 * runs is in this bundle, which is what the Chrome Web Store's Manifest V3
 * requirements ask for.
 */
import { build } from "esbuild";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("..", import.meta.url));
const src = path.join(root, "src");
const dist = path.join(root, "dist");

const shared = {
  bundle: true,
  target: "chrome116",
  platform: "browser",
  legalComments: "none",
  logLevel: "info",
};

async function main() {
  const watch = process.argv.includes("--watch");

  await rm(dist, { recursive: true, force: true });
  await mkdir(dist, { recursive: true });

  await Promise.all([
    // Service worker: ESM, because the manifest declares type: "module".
    build({
      ...shared,
      entryPoints: [path.join(src, "background/index.ts")],
      outfile: path.join(dist, "background.js"),
      format: "esm",
      minify: !watch,
    }),
    // Content script: IIFE. Injected into an arbitrary page, so it must not
    // leak a single global name into that page's scope.
    build({
      ...shared,
      entryPoints: [path.join(src, "content/index.ts")],
      outfile: path.join(dist, "content.js"),
      format: "iife",
      minify: !watch,
    }),
    // Popup: ESM, loaded by popup.html from the extension origin.
    build({
      ...shared,
      entryPoints: [path.join(src, "popup/index.ts")],
      outfile: path.join(dist, "popup.js"),
      format: "esm",
      minify: !watch,
    }),
  ]);

  await cp(path.join(src, "manifest.json"), path.join(dist, "manifest.json"));
  await cp(path.join(src, "popup/popup.html"), path.join(dist, "popup.html"));
  await cp(path.join(root, "icons"), path.join(dist, "icons"), { recursive: true });

  // Fail loudly if the manifest and the bundle ever disagree about filenames.
  const manifest = JSON.parse(await readFile(path.join(dist, "manifest.json"), "utf8"));
  const expected = [manifest.background.service_worker, manifest.action.default_popup];
  for (const file of expected) {
    await readFile(path.join(dist, file));
  }

  await writeFile(
    path.join(dist, "BUILD.txt"),
    `built ${new Date().toISOString()}\nload dist/ via chrome://extensions in developer mode\n`,
  );

  console.log("built to dist/");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
