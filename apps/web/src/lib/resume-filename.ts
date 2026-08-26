/**
 * Naming a downloaded resume.
 *
 * Its own module, with no imports and no `use client`, because both the
 * browser download path and the MCP server route need it. `download.ts` is
 * client-only, so anything living there is unreachable from the route, which
 * is how the route ended up with a second copy of this logic that then drifted.
 */

/**
 * Trailing legal-entity words, which are noise in a filename.
 *
 * Kept deliberately short and only ever stripped from the end. "Crowe Advisory
 * LLC" is better as "Crowe Advisory", but a company whose name merely contains
 * one of these words is not ours to rewrite.
 */
const LEGAL_SUFFIXES =
  /[\s,]+(inc|inc\.|llc|l\.l\.c\.|ltd|ltd\.|limited|corp|corp\.|corporation|co|co\.|company|plc|gmbh|s\.a\.|n\.v\.|pty|llp)$/i;

/** Longest a company may contribute, so one verbose employer cannot run away with the name. */
const COMPANY_LIMIT = 40;

function tidyCompany(company: string): string {
  let out = company.trim();
  // Twice, so "Example Holdings Co. Ltd" loses both rather than one.
  for (let i = 0; i < 2; i += 1) out = out.replace(LEGAL_SUFFIXES, "").trim();
  return out.slice(0, COMPANY_LIMIT).trim();
}

/**
 * "Firstname Lastname Company", sanitized into a filename.
 *
 * The role used to be in here too, and it made the name unusable. A real one:
 *
 *   Hemnaath_Balasubramani_American Express_Campus_Graduate_masters_Summer_
 *   Internship_Program_-_2027_AI_Engineer_I,_Enterpri.pdf
 *
 * The tail is a job title cut mid-word by the length cap. Job titles are long,
 * punctuated, and frequently carry the programme name, the year and a level,
 * none of which help someone find a file. Who it is and who it is for is the
 * whole job of this name.
 *
 * Takes named parts rather than an array so no caller can quietly put the role
 * back by appending one more element.
 *
 * Note the tradeoff this accepts: two resumes for two roles at the same company
 * now share a filename, and the browser will append its own " (1)". That was
 * chosen over a name nobody can read.
 */
export function buildResumeFilename(parts: {
  person?: string | null;
  company?: string | null;
}): string {
  const company = parts.company ? tidyCompany(parts.company) : "";
  const slug = [parts.person, company]
    .filter((part): part is string => !!part && part.trim().length > 0)
    .join(" ")
    .replace(/[\\/:*?"<>|]/g, "")
    .trim()
    .replace(/\s+/g, "_")
    .slice(0, 120);
  return `${slug || "resume"}.pdf`;
}
