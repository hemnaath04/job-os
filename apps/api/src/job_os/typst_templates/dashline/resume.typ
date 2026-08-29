// "dashline", ported to Typst.
//
// Not a port of anyone else's design: the LaTeX original at
// latex_templates/dashline/template.tex.j2 is itself an original job.os
// template, written to reproduce a layout common in early-career software
// resumes (a plain-case, rule-underlined section heading; one bold line per
// entry combining title/employer/place with the date range flush right; en
// dash bullets). See that file's ATTRIBUTION.md for where the shape came from
// and what was measured off a real rendered page rather than assumed.
//
// Content arrives in data.json and is interpolated in code mode, which means it
// is never re-parsed as Typst markup. Nothing here escapes anything, and nothing
// here should start: see the note at the top of typst_render.py.

#let d = json("data.json")

// Same page and type as jakes/sb2nov: US Letter, half-inch margins, New
// Computer Modern at 11pt, no hyphenation (a resume is mostly proper nouns).
#set page(paper: "us-letter", margin: (x: 0.5in, top: 0.5in, bottom: 0.5in))
#set text(font: "New Computer Modern", size: 11pt, hyphenate: false)
#set par(justify: false, leading: 0.6em, spacing: 0.6em)
#set list(indent: 0pt, body-indent: 0.4em, spacing: 0.45em, marker: [–])

#let small = 10pt
#let bullet-indent = 0.2in

#let maybe-link(url, body) = if url == "" { body } else { link(url, body) }

// The rule is the block's own bottom border, same technique as jakes' port,
// but the title itself is plain bold rather than small caps: the reference
// layout this reproduces sets its headings in ordinary title case.
#let section(title) = block(
  width: 100%,
  above: 11pt,
  below: 6pt,
  inset: (bottom: 3pt),
  stroke: (bottom: 0.4pt),
  text(size: 13pt, weight: "bold", title),
)

// One bold (or otherwise styled) line with the date range flush right. Every
// entry in this template -- education, work, projects -- opens with exactly
// one of these and nothing else, which is the trait that distinguishes this
// layout from jakes' two-line entry header.
#let entry-line(main, date) = block(
  width: 100%,
  breakable: false,
  above: 8pt,
  below: 2pt,
  grid(
    columns: (1fr, auto),
    column-gutter: 1em,
    main, align(right, date),
  ),
)

#let bullets(items) = if items.len() > 0 {
  block(
    inset: (left: bullet-indent),
    above: 3pt,
    below: 5pt,
    text(size: small, list(..items)),
  )
}

// ---------------------------------------------------------------------------
// Heading
// ---------------------------------------------------------------------------

#align(center, {
  text(size: 24.88pt, weight: "bold", d.name)
  linebreak()
  v(2pt)
  text(
    size: small,
    d.contact
      .map(c => box(if c.url == "" { c.text } else { link(c.url, c.text) }))
      .join([ | ]),
  )
})
#v(-4pt)

// ---------------------------------------------------------------------------
// Body
// ---------------------------------------------------------------------------

#if d.summary != "" {
  section[Summary]
  text(size: small, d.summary)
}

#if d.education.len() > 0 {
  section[Education]
  for school in d.education {
    let head = school.institution
    if school.location != "" { head = head + ", " + school.location }
    entry-line(strong(head), school.dates)
    if school.degree != "" {
      text(size: small, school.degree)
      linebreak()
    }
    if school.bullets.len() > 0 or school.score != "" {
      text(size: small, {
        if school.bullets.len() > 0 { "Relevant Coursework: " + school.bullets.join(", ") }
        if school.bullets.len() > 0 and school.score != "" { [ | ] }
        if school.score != "" { strong("CGPA: " + school.score) }
      })
      linebreak()
    }
  }
}

// Certifications fold into this same section rather than getting a heading of
// their own -- the reference layout this reproduces has no separate
// Certifications section, just a trailing "Certifications:" line inside Skills.
#if d.skills.len() > 0 or d.certificates.len() > 0 {
  section[Skills]
  block(above: 6pt, text(size: small, {
    for (i, group) in d.skills.enumerate() {
      strong(group.name)
      [: ]
      group.keywords_line
      if i + 1 < d.skills.len() or d.certificates.len() > 0 { linebreak() }
    }
    if d.certificates.len() > 0 {
      strong("Certifications")
      [: ]
      d.certificates
        .map(cert => {
          let piece = cert.name
          if cert.issuer != "" { piece = piece + ", " + cert.issuer }
          if cert.date != "" { piece = piece + " (" + cert.date + ")" }
          piece
        })
        .join("; ")
    }
  }))
}

#if d.work.len() > 0 {
  section[Work Experience]
  for job in d.work {
    let head = job.company
    if job.position != "" { head = head + " | " + job.position }
    if job.location != "" { head = head + " | " + job.location }
    entry-line(strong(head), job.dates)
    if job.bullets.len() > 0 {
      bullets(job.bullets)
    } else if job.summary != "" {
      bullets((job.summary,))
    }
  }
}

#if d.projects.len() > 0 {
  section[Projects]
  for project in d.projects {
    entry-line(strong(maybe-link(project.url, project.name)), project.dates)
    if project.meta_line != "" {
      text(size: small, emph(project.meta_line))
      linebreak()
    }
    if project.bullets.len() > 0 {
      bullets(project.bullets)
    } else if project.description != "" {
      bullets((project.description,))
    }
  }
}
