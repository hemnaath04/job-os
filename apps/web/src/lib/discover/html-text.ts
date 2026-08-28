/**
 * Turning a posting's body into something a card can render.
 *
 * This lived inside ./no-key-sources, private to the three ATS fetchers that
 * needed it, and that was exactly the problem: the two paths that read a
 * payload whose shape nobody declared -- ./custom-fetch's alias reader and the
 * board feeds built on top of it -- took the description as-is. Those sources
 * ship HTML as often as the ATS boards do, so a card opened with a literal
 * `<div>` (freehire.me's Reddit rows) or `<h2>Overview</h2>` (the open board
 * feeds), and the fit scorer read tag names as if they were job requirements.
 *
 * One module, imported by both, rather than a second copy: the entity table
 * and the block-tag list are the whole substance of this, and two of them
 * would drift the first time a source shipped a `&hellip;`.
 */

/** Ceiling on a rendered description. Long enough for a card and a fit score. */
export const MAX_DESCRIPTION_CHARS = 6_000;

const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
  ndash: "-",
  mdash: "-",
  hellip: "...",
  rsquo: "'",
  lsquo: "'",
  rdquo: '"',
  ldquo: '"',
  bull: "*",
};

export function decodeEntities(input: string): string {
  return input.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (match, body: string) => {
    if (body.startsWith("#x") || body.startsWith("#X")) {
      const code = Number.parseInt(body.slice(2), 16);
      return Number.isNaN(code) ? match : String.fromCodePoint(code);
    }
    if (body.startsWith("#")) {
      const code = Number.parseInt(body.slice(1), 10);
      return Number.isNaN(code) ? match : String.fromCodePoint(code);
    }
    return NAMED_ENTITIES[body.toLowerCase()] ?? match;
  });
}

export function collapseWhitespace(text: string): string {
  return text
    .replace(/\r/g, "")
    .replace(/[ \t ]+/g, " ")
    .replace(/ ?\n ?/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const lastSpace = cut.lastIndexOf(" ");
  return (lastSpace > max * 0.8 ? cut.slice(0, lastSpace) : cut).trimEnd();
}

/**
 * Greenhouse returns `content` as entity-encoded HTML (&lt;p&gt;...), while
 * Lever / Ashby / Remotive / RemoteOK / the open feeds return real HTML.
 * Detect the encoded case and unwrap it once before stripping tags.
 */
export function htmlToText(
  html: string | null | undefined,
  max: number = MAX_DESCRIPTION_CHARS,
): string {
  if (!html) return "";
  let text = html;
  if (text.includes("&lt;") && !text.includes("<")) text = decodeEntities(text);
  text = text
    .replace(/<(script|style)[\s\S]*?<\/\1>/gi, " ")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|li|ul|ol|h[1-6]|tr|table|section)>/gi, "\n")
    .replace(/<li[^>]*>/gi, "- ")
    .replace(/<[^>]+>/g, " ");
  return truncate(collapseWhitespace(decodeEntities(text)), max);
}

/**
 * Any description, from any source, ready to render.
 *
 * Unlike `htmlToText` this does not assume its input is markup: most feeds send
 * plain text and a few send HTML, in the same field, and the caller reading
 * them by alias cannot know which. Markup is stripped when it is there and the
 * text is left alone when it is not, so a plain description with a stray `<`
 * in it ("<100ms latency") keeps its angle bracket instead of losing the rest
 * of the sentence to a tag that was never opened.
 */
export function toDisplayText(
  value: unknown,
  max: number = MAX_DESCRIPTION_CHARS,
): string {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return "";
  return looksLikeHtml(raw)
    ? htmlToText(raw, max)
    : truncate(collapseWhitespace(decodeEntities(raw)), max);
}

/**
 * A real tag, not a comparison operator.
 *
 * Requires a `<` immediately followed by a tag name or a closing slash, which
 * "under <100ms" and "a < b" do not satisfy. Entity-encoded markup counts too,
 * since that is how Greenhouse and several feeds ship theirs.
 */
export function looksLikeHtml(text: string): boolean {
  return /<\/?[a-z][a-z0-9-]*(\s[^<>]*)?\/?>/i.test(text) || /&lt;\/?[a-z]/i.test(text);
}
