// Jake's Resume, ported to Typst.
//
// Original author: Jake Gutierrez, https://github.com/jakegut/resume, MIT.
// Based in turn on Sourabh Bajaj's resume. See ATTRIBUTION.md for the licence
// and for what this port does differently from the LaTeX original.
//
// Content arrives in data.json and is interpolated in code mode, which means it
// is never re-parsed as Typst markup. Nothing here escapes anything, and nothing
// here should start: see the note at the top of typst_render.py.

#let d = json("data.json")

// Jake's is Computer Modern at 11pt on US Letter with half-inch margins, which
// the LaTeX original reaches by adding an inch to \textwidth and taking half an
// inch off each of \oddsidemargin and \topmargin.
#set page(paper: "us-letter", margin: (x: 0.5in, top: 0.5in, bottom: 0.5in))
#set text(
  font: "New Computer Modern",
  size: 11pt,
  // The LaTeX template sets \hyphenpenalty and \exhyphenpenalty to 10000 for a
  // reason worth keeping: a resume is mostly proper nouns, and a real render
  // produced "Mon-goDB", which reads as a typo in the one document where a typo
  // costs the most. The page is ragged right, so a line that will not fit
  // simply breaks earlier.
  hyphenate: false,
)
#set par(justify: false, leading: 0.6em, spacing: 0.6em)
#set list(indent: 0pt, body-indent: 0.4em, spacing: 0.55em, marker: [•])

#let small = 10pt

// Jake's indents the body of every section by 0.15in, via an itemize with no
// label, and indents bullets a little further inside that.
#let body-indent = 0.15in
#let bullet-indent = 0.33in

// A URL that may be absent. Every template needs this and none of them should
// have to know that an empty string means "no link".
#let maybe-link(url, body) = if url == "" { body } else { link(url, body) }

// The rule is the block's own bottom border rather than a line nudged upwards,
// which keeps it a fixed distance under the text however the title is set.
#let section(title) = block(
  width: 100%,
  above: 11pt,
  below: 6pt,
  inset: (bottom: 3pt),
  stroke: (bottom: 0.4pt),
  text(size: 12.1pt, smallcaps(title)),
)

// The four-cornered entry: title and date on the first line, subtitle and place
// on the second. The LaTeX version is a tabularx whose left column wraps, which
// is what stops a long job title from running into its date; a grid with a 1fr
// first column is the same arrangement.
#let entry(title, right-top, subtitle, right-bottom) = block(
  width: 100%,
  breakable: false,
  above: 8pt,
  below: 0pt,
  inset: (left: body-indent),
  grid(
    columns: (1fr, auto),
    column-gutter: 1em,
    row-gutter: 0.35em,
    strong(title), align(right, right-top),
    text(size: small, emph(subtitle)), align(right, text(size: small, emph(right-bottom))),
  ),
)

#let bullets(items) = if items.len() > 0 {
  block(
    inset: (left: bullet-indent),
    above: 5pt,
    below: 5pt,
    text(size: small, list(..items)),
  )
}

// ---------------------------------------------------------------------------
// Heading
// ---------------------------------------------------------------------------

#align(center, {
  text(size: 24.88pt, strong(smallcaps(d.name)))
  linebreak()
  v(1pt)
  // Each item is kept whole so a contact line long enough to wrap breaks after
  // a bar rather than before one. Only the header underlines its links, which
  // is where Jake's puts the one \underline in the document.
  text(
    size: small,
    d.contact
      .map(c => box(if c.url == "" { c.text } else { link(c.url, underline(c.text)) }))
      .join([ | ]),
  )
})

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
    entry(
      school.institution,
      school.location,
      if school.score == "" { school.degree } else { school.degree + ", GPA: " + school.score },
      school.dates,
    )
    if school.bullets.len() > 0 {
      bullets(("Coursework: " + school.bullets.join(", "),))
    }
  }
}

#if d.work.len() > 0 {
  section[Experience]
  for job in d.work {
    entry(job.position, job.dates, job.company, job.location)
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
    block(
      width: 100%,
      breakable: false,
      above: 8pt,
      below: 0pt,
      inset: (left: body-indent),
      grid(
        columns: (1fr, auto),
        column-gutter: 1em,
        {
          text(size: small, strong(maybe-link(project.url, project.name)))
          if project.keywords_line != "" {
            text(size: small)[ | #emph(project.keywords_line)]
          }
        },
        align(right, project.dates),
      ),
    )
    // A project with bullets shows them; one with only a description shows
    // that instead, so a sparse entry is never a heading with nothing under it.
    if project.bullets.len() > 0 {
      bullets(project.bullets)
    } else if project.description != "" {
      bullets((project.description,))
    }
  }
}

#if d.skills.len() > 0 {
  section[Technical Skills]
  block(inset: (left: body-indent), above: 6pt, text(size: small, {
    for (i, group) in d.skills.enumerate() {
      strong(group.name)
      [: ]
      group.keywords_line
      if i + 1 < d.skills.len() { linebreak() }
    }
  }))
}

#if d.certificates.len() > 0 {
  section[Certifications]
  block(inset: (left: body-indent), above: 6pt, text(size: small, {
    for (i, cert) in d.certificates.enumerate() {
      strong(cert.name)
      if cert.issuer != "" { [, ] + cert.issuer }
      if cert.date != "" { [ (] + cert.date + [)] }
      if i + 1 < d.certificates.len() { linebreak() }
    }
  }))
}
