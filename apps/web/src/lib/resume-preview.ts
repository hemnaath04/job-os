import type { JsonResume } from "./types";

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function dateRange(start?: string | null, end?: string | null): string {
  return [start, end || (start ? "Present" : null)]
    .filter(Boolean)
    .map(escapeHtml)
    .join(" — ");
}

function bullets(items?: string[]): string {
  if (!items?.length) return "";
  return `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

export function renderResumePreview(resume: JsonResume): string {
  const basics = resume.basics ?? {};
  const work = (resume.work ?? [])
    .map(
      (item) => `
        <article>
          <div class="row"><strong>${escapeHtml(item.position)}</strong><span>${dateRange(item.startDate, item.endDate)}</span></div>
          <div class="muted">${escapeHtml(item.name)}${item.location ? ` · ${escapeHtml(item.location)}` : ""}</div>
          ${bullets(item.highlights)}
        </article>`,
    )
    .join("");
  const projects = (resume.projects ?? [])
    .map(
      (item) => `
        <article>
          <div class="row"><strong>${escapeHtml(item.name)}</strong><span>${dateRange(item.startDate, item.endDate)}</span></div>
          ${item.description ? `<div class="muted">${escapeHtml(item.description)}</div>` : ""}
          ${bullets(item.highlights)}
        </article>`,
    )
    .join("");
  const education = (resume.education ?? [])
    .map(
      (item) => `
        <article>
          <div class="row"><strong>${escapeHtml(item.institution)}</strong><span>${dateRange(item.startDate, item.endDate)}</span></div>
          <div class="muted">${escapeHtml([item.studyType, item.area].filter(Boolean).join(" in "))}</div>
        </article>`,
    )
    .join("");
  const skills = (resume.skills ?? [])
    .map(
      (group) =>
        `<div><strong>${escapeHtml(group.name)}:</strong> ${escapeHtml(group.keywords.join(", "))}</div>`,
    )
    .join("");

  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  *{box-sizing:border-box} body{margin:0;background:#fff;color:#141414;font-family:Inter,Arial,sans-serif;font-size:11px;line-height:1.42}
  main{width:8.5in;min-height:11in;margin:0 auto;padding:.52in .58in}
  header{text-align:center;border-bottom:1px solid #333;padding-bottom:10px}
  h1{font-size:24px;line-height:1.05;margin:0 0 5px;letter-spacing:-.02em}
  h2{font-size:11px;text-transform:uppercase;letter-spacing:.12em;margin:15px 0 6px;border-bottom:1px solid #777;padding-bottom:2px}
  p{margin:6px 0}.contact{font-size:10px;color:#333}.row{display:flex;justify-content:space-between;gap:20px}
  article{margin:0 0 8px}.muted{color:#444}ul{margin:4px 0 0;padding-left:16px}li{margin:1px 0}
  @media(max-width:760px){main{width:100%;padding:24px}}
</style></head><body><main>
  <header>
    <h1>${escapeHtml(basics.name)}</h1>
    <div class="contact">${[basics.email, basics.phone, basics.url].filter(Boolean).map(escapeHtml).join(" · ")}</div>
  </header>
  ${basics.summary ? `<p>${escapeHtml(basics.summary)}</p>` : ""}
  ${work ? `<section><h2>Experience</h2>${work}</section>` : ""}
  ${projects ? `<section><h2>Projects</h2>${projects}</section>` : ""}
  ${education ? `<section><h2>Education</h2>${education}</section>` : ""}
  ${skills ? `<section><h2>Skills</h2>${skills}</section>` : ""}
</main></body></html>`;
}
