// sb2nov's resume, ported to Typst.
//
// Original author: Sourabh Bajaj, https://github.com/sb2nov/resume, MIT.
// See ATTRIBUTION.md for the licence and for what this port does differently
// from the LaTeX original.
//
// Content arrives in data.json and is interpolated in code mode, which means it
// is never re-parsed as Typst markup. Nothing here escapes anything, and nothing
// here should start: see the note at the top of typst_render.py.

#let d = json("data.json")

// Same page as Jake's, which this shares a lineage with: Computer Modern at
// 11pt on US Letter, half-inch margins.
#set page(paper: "us-letter", margin: (x: 0.5in, top: 0.5in, bottom: 0.5in))
#set text(
  font: "New Computer Modern",
  size: 11pt,
  // A resume is mostly proper nouns, and the page is ragged right, so a line
  // that will not fit breaks earlier rather than hyphenating a company name.
  hyphenate: false,
)
#set par(justify: false, leading: 0.6em, spacing: 0.6em)

#let small = 10pt

#let maybe-link(url, body) = if url == "" { body } else { link(url, body) }

#let section(title) = block(
  width: 100%,
  above: 11pt,
  below: 6pt,
  inset: (bottom: 3pt),
  stroke: (bottom: 0.4pt),
  text(size: 12.1pt, smallcaps(title)),
)

// Where Jake's hides the entry list's markers, sb2nov leaves them on, so an
// entry is itself a bullet and the detail under it is a nested ring.
#let entries(items) = block(
  width: 100%,
  above: 6pt,
  list(indent: 0pt, body-indent: 0.5em, spacing: 0.7em, marker: [•], ..items),
)

#let sub-bullets(items) = if items.len() > 0 {
  block(
    above: 4pt,
    below: 2pt,
    text(size: small, list(indent: 0.7em, body-indent: 0.5em, spacing: 0.45em, marker: [◦], ..items)),
  )
}

// The four-cornered entry, as a tabularx whose left column wraps so a long
// title cannot run into its date.
#let entry(title, right-top, subtitle, right-bottom) = block(
  width: 100%,
  breakable: false,
  grid(
    columns: (1fr, auto),
    column-gutter: 1em,
    row-gutter: 0.35em,
    strong(title), align(right, right-top),
    text(size: small, emph(subtitle)), align(right, text(size: small, emph(right-bottom))),
  ),
)

// ---------------------------------------------------------------------------
// Heading
// ---------------------------------------------------------------------------
//
// A two-column block rather than Jake's centred stack: name and links down the
// left, the ways to reach this person down the right.

// Parenthesised because a method chain broken across lines would otherwise end
// at the first complete expression, leaving this bound to the bare array.
#let profile-line = (
  d.profiles.map(p => box(maybe-link(p.url, p.label))).join([ | ])
)

#block(
  width: 100%,
  grid(
    columns: (1fr, auto),
    column-gutter: 1em,
    row-gutter: 0.4em,

    strong(text(size: 14.4pt, maybe-link(d.website, d.name))),
    align(right, if d.email != "" { [Email: #maybe-link(d.email_url, d.email)] }),

    if d.website != "" { maybe-link(d.website, d.website_label) } else { d.location },
    align(right, if d.phone != "" { [Mobile: #d.phone] }),

    if d.profiles.len() > 0 { profile-line },
    align(right, if d.website != "" and d.location != "" { d.location }),
  ),
)

// ---------------------------------------------------------------------------
// Body
// ---------------------------------------------------------------------------

#if d.summary != "" {
  section[Summary]
  text(size: small, d.summary)
}

#if d.education.len() > 0 {
  section[Education]
  entries(d.education.map(school => {
    entry(
      school.institution,
      school.location,
      if school.score == "" { school.degree } else { school.degree + "; GPA: " + school.score },
      school.dates,
    )
  }))
}

#if d.work.len() > 0 {
  section[Experience]
  entries(d.work.map(job => {
    entry(job.company, job.location, job.position, job.dates)
    if job.bullets.len() > 0 {
      sub-bullets(job.bullets)
    } else if job.summary != "" {
      sub-bullets((job.summary,))
    }
  }))
}

#if d.projects.len() > 0 {
  section[Projects]
  entries(d.projects.map(project => {
    entry(
      maybe-link(project.url, project.name),
      project.dates,
      if project.meta_line != "" { project.meta_line } else { project.description },
      "",
    )
    // The description only earns its own line when the subtitle went to the
    // technology list instead, otherwise it would print twice.
    if project.bullets.len() > 0 {
      sub-bullets(project.bullets)
    } else if project.description != "" and project.meta_line != "" {
      sub-bullets((project.description,))
    }
  }))
}

#if d.skills.len() > 0 {
  section[Skills Summary]
  entries(d.skills.map(group => text(size: small, {
    strong(group.name)
    [: ]
    group.keywords_line
  })))
}

#if d.certificates.len() > 0 or d.awards.len() > 0 {
  section[Certifications and Awards]
  entries(
    d.certificates.map(cert => text(size: small, {
      cert.name
      if cert.issuer != "" { [, ] + cert.issuer }
      if cert.date != "" { [ (] + cert.date + [)] }
    }))
      + d.awards.map(award => text(size: small, {
        award.title
        if award.awarder != "" { [, ] + award.awarder }
      })),
  )
}
