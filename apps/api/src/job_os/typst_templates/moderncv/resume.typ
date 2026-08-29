// ModernCV, banking style, ported to Typst.
//
// Original authors: Xavier Danaux and the moderncv maintainers,
// https://ctan.org/pkg/moderncv. The class is LPPL 1.3c. See ATTRIBUTION.md,
// and read the honest assessment there before enabling this one: it is the only
// port in this directory with no vendored class to measure against, so it is a
// reconstruction from the class's output rather than a transcription.
//
// Content arrives in data.json and is interpolated in code mode, which means it
// is never re-parsed as Typst markup. Nothing here escapes anything, and nothing
// here should start: see the note at the top of typst_render.py.

#let d = json("data.json")

// moderncvcolorblue.sty, read out of the file Tectonic's bundle ships:
//   color1 = rgb(0.22, 0.45, 0.70), color2 = rgb(0.45, 0.45, 0.45).
#let c-accent = rgb(56, 115, 179)
#let c-grey = rgb(115, 115, 115)

// \usepackage[scale=0.8,top=1.4cm,bottom=1.4cm]{geometry}: the text block is
// four fifths of the paper, so the side margins are a tenth each.
#set page(
  paper: "us-letter",
  margin: (x: 10%, top: 1.4cm, bottom: 1.4cm),
  footer: context align(right, text(size: 8pt, style: "italic", fill: c-grey, {
    str(counter(page).get().first())
    [/]
    str(counter(page).final().first())
  })),
)
// The template passes the `sans` option, which in moderncv means Latin Modern
// Sans rather than the roman default.
#set text(font: "Latin Modern Sans", size: 11pt, hyphenate: false)
#set par(justify: false, leading: 0.62em, spacing: 0.62em)

#let maybe-link(url, body) = if url == "" { body } else { link(url, body) }

// \moderncvicons{letters}: the letter icon set rather than Font Awesome,
// because under this engine the Font Awesome faces came out with no ToUnicode
// map and a parser read a run of junk beside the contact line. The letters are
// ordinary glyphs and extract as text. Kept for the same reason here.
#let letter-icon(kind) = {
  let mark = if kind == "phone" { "M" } else if kind == "email" { "E" } else if (
    kind == "website"
  ) { "W" } else if kind == "linkedin" { "in" } else if kind == "github" { "gh" } else { "" }
  if mark != "" { text(weight: "bold", mark) }
}

#let section(title) = block(
  width: 100%,
  above: 14pt,
  below: 7pt,
  {
    text(size: 14pt, weight: "bold", fill: c-accent, title)
    v(-6pt)
    line(length: 100%, stroke: 0.6pt + c-accent.lighten(45%))
  },
)

// \cventry in the banking style: two ruled-off lines, the organisation and the
// place in bold, the role and the dates italic beneath.
#let cventry(dates, title, org, location, body) = block(
  width: 100%,
  above: 9pt,
  below: 0pt,
  breakable: false,
  {
    grid(
      columns: (1fr, auto),
      column-gutter: 1em,
      row-gutter: 0.55em,
      text(weight: "bold", org),
      align(right, text(weight: "bold", location)),
      text(style: "italic", title),
      align(right, text(style: "italic", dates)),
    )
    body
  },
)

#let bullets(items) = if items.len() > 0 {
  block(
    above: 5pt,
    below: 0pt,
    text(size: 10pt, list(
      indent: 0.6em,
      body-indent: 0.5em,
      spacing: 0.45em,
      marker: text(fill: c-accent, size: 8pt)[#sym.circle.stroked],
      ..items,
    )),
  )
}

// \cvitem{label}{text}: in the banking style the label runs inline in bold
// rather than sitting in the hints column the other styles use.
#let cvitem(label, body) = block(above: 5pt, below: 0pt, {
  if label != "" {
    text(weight: "bold", label)
    [: ]
  }
  body
})

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

#align(center, {
  text(size: 20pt, weight: "bold", fill: c-accent, d.name)
  if d.headline != "" {
    text(size: 20pt, fill: c-grey, " | " + d.headline)
  }
})

#if d.location != "" {
  align(center, block(above: 6pt, below: 0pt, text(size: 9pt, fill: c-grey, d.location)))
}

#if d.contact.len() > 0 {
  let reachable = d.contact.filter(c => c.kind != "location")
  align(center, block(
    above: 5pt,
    below: 0pt,
    text(size: 9pt, reachable.map(c => box({
      let mark = letter-icon(c.kind)
      if mark != none { mark; h(0.35em) }
      maybe-link(c.url, c.text)
    })).join([ #sym.bullet ])),
  ))
}

#v(10pt)

// ---------------------------------------------------------------------------
// Body
// ---------------------------------------------------------------------------

#if d.summary != "" {
  section[Summary]
  cvitem("", d.summary)
}

#if d.work.len() > 0 {
  section[Experience]
  for job in d.work {
    cventry(
      job.dates,
      job.position,
      job.company,
      job.location,
      if job.bullets.len() > 0 {
        bullets(job.bullets)
      } else if job.summary != "" {
        block(above: 5pt, text(size: 10pt, job.summary))
      },
    )
  }
}

#if d.projects.len() > 0 {
  section[Projects]
  for project in d.projects {
    // The LaTeX template puts the project name on the bold line and the stack
    // on the italic one, which is the reverse of how \cventry reads for a job.
    cventry(
      project.dates,
      project.meta_line,
      maybe-link(project.url, project.name),
      "",
      if project.bullets.len() > 0 {
        bullets(
          (if project.description != "" { (project.description,) } else { () })
            + project.bullets,
        )
      } else if project.description != "" {
        block(above: 5pt, text(size: 10pt, project.description))
      },
    )
  }
}

#if d.education.len() > 0 {
  section[Education]
  for school in d.education {
    // The grade is \cventry's fifth argument, which the banking style prints on
    // the end of the degree line rather than on one of its own.
    cventry(
      school.dates,
      school.degree + if school.score != "" { ", GPA " + school.score } else { "" },
      school.institution,
      school.location,
      if school.bullets.len() > 0 {
        block(
          above: 4pt,
          below: 0pt,
          text(size: 10pt, "Coursework: " + school.bullets.join(", ")),
        )
      },
    )
  }
}

#if d.skills.len() > 0 {
  section[Skills]
  for group in d.skills {
    cvitem(group.name, group.keywords_line)
  }
}

#if d.certificates.len() > 0 {
  section[Certifications]
  for cert in d.certificates {
    cvitem(cert.date, cert.name + if cert.issuer != "" { ", " + cert.issuer } else { "" })
  }
}

#if d.awards.len() > 0 {
  section[Honors]
  for award in d.awards {
    cvitem("", award.title + if award.awarder != "" { ", " + award.awarder } else { "" })
  }
}

#if d.languages.len() > 0 {
  section[Languages]
  for language in d.languages {
    cvitem(language.language, language.fluency)
  }
}
