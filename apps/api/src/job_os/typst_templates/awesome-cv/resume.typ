// Awesome-CV, ported to Typst.
//
// Original author: Claud D. Park (posquit0), https://github.com/posquit0/Awesome-CV.
// The class is LPPL 1.3c. This document was written against the class's own
// documented commands rather than derived from the upstream examples, which are
// CC BY-SA, exactly as the LaTeX version in latex_templates/awesome-cv was.
// See ATTRIBUTION.md.
//
// Content arrives in data.json and is interpolated in code mode, which means it
// is never re-parsed as Typst markup. Nothing here escapes anything, and nothing
// here should start: see the note at the top of typst_render.py.

#let d = json("data.json")

// Every colour and size below is read off awesome-cv-tectonic.cls, not guessed.
#let c-text = rgb("#333333")        // \colorlet{text}{darkgray}
#let c-graytext = rgb("#5d5d5d")    // \colorlet{graytext}{gray}
#let c-lighttext = rgb("#999999")   // \colorlet{lighttext}{lightgray}
#let c-darktext = rgb("#414141")
#let c-divider = rgb("#5d5d5d")     // \colorlet{sectiondivider}{gray}
#let c-awesome = rgb("#131a28")     // the template picks awesome-darknight

#let body-font = "Source Sans Pro"
#let header-font = "Roboto"

// \geometry{left=1.4cm, top=.8cm, right=1.4cm, bottom=1.2cm}
#set page(paper: "us-letter", margin: (left: 1.4cm, right: 1.4cm, top: 0.8cm, bottom: 1.2cm))
#set text(font: body-font, size: 9pt, fill: c-text, hyphenate: false)
#set par(justify: false, leading: 0.55em, spacing: 0.55em)

#let maybe-link(url, body) = if url == "" { body } else { link(url, body) }

// Font Awesome 5 Free. The class draws these same glyphs, and the template's
// own ATS note already says out loud that they are glyphs and that a parser may
// read a stray character beside the address. Kept because they are the design.
#let fa-solid(code) = text(font: "Font Awesome 5 Free Solid", code)
#let fa-brand(code) = text(font: "Font Awesome 5 Brands", code)

#let icon-for(kind) = {
  if kind == "phone" { fa-solid("\u{f10b}") } else if kind == "email" {
    fa-solid("\u{f0e0}")
  } else if kind == "website" { fa-solid("\u{f015}") } else if kind == "github" {
    fa-brand("\u{f092}")
  } else if kind == "linkedin" { fa-brand("\u{f08c}") } else { fa-solid("\u{f0c1}") }
}

// \sectionstyle: 16pt bold, then a rule running from the title to the right
// margin. acvSectionTopSkip is 3mm and acvSectionContentTopSkip 2.5mm.
#let section(title) = block(
  width: 100%,
  above: 6mm,
  below: 2.5mm,
  grid(
    columns: (auto, 1fr),
    column-gutter: 0.6em,
    align: (left + horizon, left + horizon),
    text(font: body-font, size: 16pt, weight: "bold", fill: c-awesome, title),
    line(length: 100%, stroke: 0.4pt + c-divider),
  ),
)

// \cventry, whose argument order is position first and title second: the bold
// line is the employer or the school, and the small-caps line beneath is the
// role or the degree.
#let cventry(position, title, location, dates, body) = block(
  width: 100%,
  above: 5mm,
  below: 0pt,
  breakable: false,
  {
    grid(
      columns: (1fr, auto),
      column-gutter: 1em,
      row-gutter: 0.45em,
      text(size: 10pt, weight: "bold", fill: c-darktext, title),
      align(right, text(size: 9pt, weight: 300, style: "italic", fill: c-awesome, location)),
      text(size: 8pt, fill: c-graytext, smallcaps(position)),
      align(right, text(size: 8pt, weight: 300, style: "italic", fill: c-graytext, dates)),
    )
    body
  },
)

#let bullets(items) = if items.len() > 0 {
  block(
    above: 2mm,
    below: 0pt,
    text(
      size: 9pt,
      weight: 300,
      list(indent: 0.4em, body-indent: 0.5em, spacing: 0.4em, marker: [•], ..items),
    ),
  )
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

#align(center, {
  // 32pt, the given name light and the family name bold.
  text(font: header-font, size: 32pt, weight: 300, fill: c-graytext, d.first_name + " ")
  text(font: header-font, size: 32pt, weight: "bold", fill: c-text, d.last_name)

  if d.headline != "" {
    linebreak()
    v(2.6mm, weak: true)
    text(font: body-font, size: 7.6pt, fill: c-awesome, smallcaps(d.headline))
  }
  if d.location != "" {
    linebreak()
    v(1.4mm, weak: true)
    text(font: header-font, size: 8pt, style: "italic", fill: c-lighttext, d.location)
  }

  // The social line, which is the contact list minus the address already shown.
  let social = d.contact.filter(c => c.kind != "location")
  if social.len() > 0 {
    linebreak()
    v(2.2mm, weak: true)
    text(
      font: header-font,
      size: 6.8pt,
      fill: c-text,
      social
        .map(c => box({
          icon-for(c.kind)
          h(0.35em)
          maybe-link(c.url, c.text)
        }))
        .join(text(fill: c-lighttext)[#h(0.6em) | #h(0.6em)]),
    )
  }
})

#v(2mm)

// ---------------------------------------------------------------------------
// Body
// ---------------------------------------------------------------------------

#if d.summary != "" {
  section[Summary]
  text(size: 9pt, weight: 300, d.summary)
}

#if d.work.len() > 0 {
  section[Experience]
  for job in d.work {
    cventry(
      job.position,
      job.company,
      job.location,
      job.dates,
      if job.bullets.len() > 0 {
        bullets(job.bullets)
      } else if job.summary != "" {
        block(above: 1.5mm, text(size: 9pt, weight: 300, job.summary))
      },
    )
  }
}

#if d.projects.len() > 0 {
  section[Projects]
  for project in d.projects {
    cventry(
      project.meta_line,
      maybe-link(project.url, project.name),
      "",
      project.dates,
      if project.bullets.len() > 0 {
        // The description leads the list, as it does in the LaTeX version.
        bullets(
          (if project.description != "" { (project.description,) } else { () })
            + project.bullets,
        )
      } else if project.description != "" {
        block(above: 1.5mm, text(size: 9pt, weight: 300, project.description))
      },
    )
  }
}

#if d.education.len() > 0 {
  section[Education]
  for school in d.education {
    cventry(
      if school.score == "" { school.degree } else { school.degree + ", GPA " + school.score },
      school.institution,
      school.location,
      school.dates,
      if school.bullets.len() > 0 {
        bullets(("Coursework: " + school.bullets.join(", "),))
      },
    )
  }
}

// \cvskill is a two-column table: a right-aligned bold label, then the values.
#let skill-rows(rows) = block(
  width: 100%,
  above: 0pt,
  grid(
    columns: (0.181 * 100%, 1fr),
    column-gutter: 1em,
    row-gutter: 0.5em,
    ..rows
      .map(row => (
        align(right, text(size: 10pt, weight: "bold", fill: c-darktext, row.at(0))),
        text(size: 9pt, weight: 300, row.at(1)),
      ))
      .flatten(),
  ),
)

#if d.skills.len() > 0 {
  section[Skills]
  skill-rows(d.skills.map(group => (group.name, group.keywords_line)))
}

// \cvhonors is a three-column table: C{1.5cm} for the date, the title, then
// R{2.5cm} for where it came from.
#if d.certificates.len() > 0 or d.awards.len() > 0 {
  section[Certifications and Honors]
  let rows = (
    d.certificates.map(cert => (cert.date, cert.name, cert.issuer))
      + d.awards.map(award => ("", award.title, award.awarder))
  )
  block(
    width: 100%,
    above: 0pt,
    grid(
      columns: (1.5cm, 1fr, 2.5cm),
      column-gutter: 1em,
      row-gutter: 0.5em,
      ..rows
        .map(row => (
          align(center, text(size: 9pt, fill: c-graytext, row.at(0))),
          text(size: 9pt, weight: "bold", fill: c-darktext, row.at(1)),
          align(right, text(size: 9pt, weight: 300, style: "italic", fill: c-awesome, row.at(2))),
        ))
        .flatten(),
    ),
  )
}

#if d.languages.len() > 0 {
  section[Languages]
  skill-rows(d.languages.map(language => (language.language, language.fluency)))
}
