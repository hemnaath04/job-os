/**
 * Save a real application form as a test fixture.
 *
 * Usage:
 *   node scripts/save-fixture.mjs <url> <name> [formSelector]
 *
 * Tests run against saved HTML rather than live forms, for three reasons: the
 * suite has to be deterministic, hammering a real employer's application
 * endpoint from CI is rude, and a fixture is a record of what the markup
 * actually looked like on the day rather than what it looks like now.
 *
 * The extractor keeps the form subtree and throws away scripts, styles,
 * inline event handlers and anything else that would either execute or bloat
 * the file. A 700 kB page becomes a readable few tens of kB of structure,
 * which is the part the adapters care about.
 *
 * This script only ever issues GET requests. It never posts a form.
 */
import { JSDOM } from "jsdom";
import { writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

const FIXTURES = fileURLToPath(new URL("../tests/fixtures/", import.meta.url));

/** Elements whose content is never structure we care about. */
const DROP = ["script", "style", "noscript", "svg", "iframe", "link", "picture", "img"];

async function main() {
  const [url, name, formSelector] = process.argv.slice(2);
  if (!url || !name) {
    console.error("usage: node scripts/save-fixture.mjs <url> <name> [formSelector]");
    process.exit(1);
  }

  const response = await fetch(url, { headers: { "user-agent": UA, accept: "text/html" } });
  if (!response.ok) {
    console.error(`GET ${url} returned ${response.status}`);
    process.exit(1);
  }

  const html = await response.text();
  const dom = new JSDOM(html);
  const doc = dom.window.document;

  const root =
    (formSelector ? doc.querySelector(formSelector) : null) ??
    pickLargestForm(doc) ??
    doc.body;

  clean(root);

  const out = `<!doctype html>
<!--
  Fixture captured from ${url}
  on ${new Date().toISOString().slice(0, 10)} with scripts and styling stripped.
  Real markup, reduced to the structure the adapters read.
-->
<html><head><meta charset="utf-8"><title>${name} fixture</title></head>
<body>
${root.outerHTML}
</body></html>
`;

  const file = path.join(FIXTURES, `${name}.html`);
  await writeFile(file, out, "utf8");
  console.log(`wrote ${file} (${out.length} bytes)`);
}

function pickLargestForm(doc) {
  const forms = Array.from(doc.querySelectorAll("form"));
  if (forms.length === 0) return null;
  return forms
    .map((form) => ({
      form,
      n: form.querySelectorAll("input, textarea, select").length,
    }))
    .sort((a, b) => b.n - a.n)[0].form;
}

/** Strip everything that executes, styles, or merely bloats. */
function clean(root) {
  for (const el of root.querySelectorAll(DROP.join(","))) el.remove();

  for (const el of root.querySelectorAll("*")) {
    for (const attr of Array.from(el.attributes)) {
      const n = attr.name.toLowerCase();
      // Inline handlers would be dead weight at best and confusing at worst.
      if (n.startsWith("on")) el.removeAttribute(attr.name);
      // Styling carries no signal for a label-and-ARIA based adapter, and
      // dropping it is most of the size win.
      if (n === "style") el.removeAttribute(attr.name);
    }
  }

  // Collapse runs of whitespace-only text nodes so the file stays readable.
  const walker = root.ownerDocument.createTreeWalker(root, 4 /* SHOW_TEXT */);
  const empties = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if ((node.nodeValue ?? "").trim() === "") empties.push(node);
  }
  for (const node of empties) node.remove();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
