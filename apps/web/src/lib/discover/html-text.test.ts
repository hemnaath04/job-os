import assert from "node:assert/strict";
import { test } from "node:test";
import { htmlToText, looksLikeHtml, toDisplayText } from "./html-text.ts";

// The two cards that shipped their markup to the screen, verbatim.
const REDDIT_VIA_FREEHIRE =
  '<div class="job"><p>Reddit is looking for a backend intern.</p>' +
  "<ul><li>Python</li><li>Kubernetes</li></ul></div>";
const COTIVITI_INTERN =
  "<h2>Overview</h2><p>Cotiviti is hiring an Intern AI Engineer.</p>";

test("a description that opens with a div does not render its div", () => {
  const text = toDisplayText(REDDIT_VIA_FREEHIRE);
  assert.ok(!text.includes("<"), text);
  assert.ok(text.startsWith("Reddit is looking for a backend intern."), text);
  assert.match(text, /- Python/);
});

test("a description that opens with a heading keeps the heading's words", () => {
  const text = toDisplayText(COTIVITI_INTERN);
  assert.equal(text, "Overview\nCotiviti is hiring an Intern AI Engineer.");
});

test("entity-encoded markup is unwrapped before it is stripped", () => {
  assert.equal(
    toDisplayText("&lt;p&gt;We use Python &amp; Go.&lt;/p&gt;"),
    "We use Python & Go.",
  );
});

test("plain text is left alone, angle brackets and all", () => {
  // The reason this is not just "strip anything between angle brackets": a
  // latency requirement is not a tag, and treating it as one eats the rest of
  // the sentence.
  const plain = "Serve p99 under <100ms at 5 > 3 replicas.";
  assert.equal(toDisplayText(plain), plain);
  assert.equal(looksLikeHtml(plain), false);
});

test("script and style contents never reach the card", () => {
  const text = toDisplayText(
    "<style>.a{color:red}</style><p>Real body.</p><script>alert(1)</script>",
  );
  assert.equal(text, "Real body.");
});

test("an empty or non-string description is an empty string", () => {
  assert.equal(toDisplayText(null), "");
  assert.equal(toDisplayText(undefined), "");
  assert.equal(toDisplayText(42), "");
  assert.equal(toDisplayText("   "), "");
});

test("the length cap cuts on a word boundary", () => {
  const long = `<p>${"alpha ".repeat(50)}</p>`;
  const text = toDisplayText(long, 40);
  assert.ok(text.length <= 40, `${text.length}`);
  assert.ok(!text.endsWith("alph"), text);
});

test("htmlToText and toDisplayText agree on real markup", () => {
  assert.equal(toDisplayText(COTIVITI_INTERN), htmlToText(COTIVITI_INTERN));
});
