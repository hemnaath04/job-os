// AltaCV, ported to Typst.
//
// Original author: LianTze Lim, https://github.com/liantze/AltaCV. The class is
// LPPL 1.3 or later. See ATTRIBUTION.md.
//
// Content arrives in data.json and is interpolated in code mode, which means it
// is never re-parsed as Typst markup. Nothing here escapes anything, and nothing
// here should start: see the note at the top of typst_render.py.

#let d = json("data.json")

// The colour scheme is the upstream sample's, which the LaTeX template uses.
#let c-purple = rgb("#3e0097")   // VividPurple
#let c-slate = rgb("#2e2e2e")    // SlateGrey, \colorlet{emphasis}
#let c-body = rgb("#666666")     // LightGrey, \colorlet{body}

// \geometry{left=1.25cm,right=1.25cm,top=1.5cm,bottom=1.5cm,columnsep=1.1cm}
#set page(paper: "us-letter", margin: (x: 1.25cm, y: 1.5cm))
// The class loads Roboto Slab as the main font and Lato as the sans, then sets
// \familydefault to the sans, so everything visible on the page is Lato.
#set text(font: "Lato", size: 10pt, fill: c-body, hyphenate: false)
#set par(justify: false, leading: 0.6em, spacing: 0.6em)

#let maybe-link(url, body) = if url == "" { body } else { link(url, body) }

#let fa-solid(code) = text(font: "Font Awesome 5 Free Solid", code)
#let fa-brand(code) = text(font: "Font Awesome 5 Brands", code)

#let icon-for(kind) = {
  if kind == "email" { fa-solid("\u{f1fa}") } else if kind == "phone" {
    fa-solid("\u{f095}")
  } else if kind == "location" { fa-solid("\u{f3c5}") } else if kind == "website" {
    fa-solid("\u{f0ac}")
  } else if kind == "github" { fa-brand("\u{f09b}") } else if kind == "linkedin" {
    fa-brand("\u{f08c}")
  } else { fa-solid("\u{f0c1}") }
}

#let calendar = fa-solid("\u{f133}")
#let marker = fa-solid("\u{f3c5}")

// \cvsection: uppercase, purple, over a purple rule.
#let cvsection(title) = block(
  width: 100%,
  above: 7pt,
  below: 6pt,
  inset: (bottom: 2pt),
  stroke: (bottom: 1pt + c-purple),
  text(size: 16pt, fill: c-purple, upper(title)),
)

// \cvevent: the role, then the organisation in purple, then a date and place
// line carrying the class's own icons.
#let cvevent(title, org, dates, location) = block(
  width: 100%,
  above: 6pt,
  below: 3pt,
  breakable: false,
  {
    block(above: 0pt, below: 3pt, text(size: 11pt, fill: c-slate, title))
    if org != "" {
      block(above: 0pt, below: 3pt, text(size: 10pt, weight: "bold", fill: c-purple, org))
    }
    if dates != "" or location != "" {
      block(above: 0pt, below: 0pt, text(size: 9pt, {
        if dates != "" [#calendar #h(0.35em) #dates]
        if dates != "" and location != "" { h(1.6em) }
        if location != "" [#marker #h(0.35em) #location]
      }))
    }
  },
)

#let bullets(items) = if items.len() > 0 {
  block(
    above: 4pt,
    below: 0pt,
    list(indent: 0.2em, body-indent: 0.5em, spacing: 0.5em, marker: text(size: 8pt)[•], ..items),
  )
}

// \divider: the dashed rule the class puts between events.
#let divider = block(
  above: 7pt,
  below: 4pt,
  line(length: 100%, stroke: (paint: rgb("#b0b0b0"), thickness: 0.5pt, dash: "dashed")),
)

// \cvtag: a keyword in a rounded outline, which is the sidebar's whole look.
#let cvtag(label) = box(
  inset: (x: 5pt, y: 3pt),
  outset: (y: 1pt),
  radius: 2pt,
  stroke: 0.5pt + c-body,
  text(size: 9pt, fill: c-slate, label),
)

// \cvachievement: an icon in the margin, a bold line, and a quieter one under it.
#let cvachievement(icon, title, detail) = block(
  width: 100%,
  above: 6pt,
  below: 0pt,
  grid(
    columns: (auto, 1fr),
    column-gutter: 0.6em,
    align: (top, top),
    text(size: 12pt, fill: c-purple, icon),
    {
      text(size: 10pt, weight: "bold", fill: c-slate, title)
      if detail != "" {
        linebreak()
        text(size: 10pt, detail)
      }
    },
  ),
)

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

#block(above: 0pt, below: 4pt, text(size: 26pt, fill: black, upper(d.name)))
#if d.headline != "" {
  block(above: 0pt, below: 6pt, text(size: 12pt, weight: "bold", fill: c-purple, d.headline))
}
// The LaTeX template calls \email, \phone, \location and then the remaining
// links in that order, so the header reads the same way here rather than in
// whatever order the render model happens to list them.
#let contact-order = ("email", "phone", "location", "website", "github", "linkedin")
#let ordered-contact = d.contact.sorted(key: c => {
  let at = contact-order.position(kind => kind == c.kind)
  if at == none { contact-order.len() } else { at }
})

#if ordered-contact.len() > 0 {
  block(
    above: 0pt,
    below: 10pt,
    text(size: 9pt, fill: c-slate, ordered-contact.map(c => box({
      icon-for(c.kind)
      h(0.35em)
      maybe-link(c.url, c.text)
    })).join(h(1.4em))),
  )
}

// ---------------------------------------------------------------------------
// The two columns
// ---------------------------------------------------------------------------
//
// \columnratio{0.62}: the history goes in the wide column, the short scannable
// material in the sidebar.

#let wide-column = {
  if d.summary != "" {
    cvsection[Summary]
    d.summary
  }

  if d.work.len() > 0 {
    cvsection[Experience]
    for (i, job) in d.work.enumerate() {
      cvevent(job.position, job.company, job.dates, job.location)
      if job.bullets.len() > 0 {
        bullets(job.bullets)
      } else if job.summary != "" {
        job.summary
      }
      if i + 1 < d.work.len() { divider }
    }
  }

  if d.projects.len() > 0 {
    cvsection[Projects]
    for (i, project) in d.projects.enumerate() {
      cvevent(
        maybe-link(project.url, project.name),
        project.keywords_line,
        project.dates,
        "",
      )
      if project.bullets.len() > 0 {
        bullets(
          (if project.description != "" { (project.description,) } else { () })
            + project.bullets,
        )
      } else if project.description != "" {
        project.description
      }
      if i + 1 < d.projects.len() { divider }
    }
  }
}

#let sidebar = {
  if d.skills.len() > 0 {
    cvsection[Skills]
    for group in d.skills {
      block(above: 5pt, below: 4pt, text(size: 10pt, weight: "bold", fill: c-purple, group.name))
      block(above: 0pt, below: 6pt, group.keywords.map(cvtag).join(h(4pt)))
    }
  }

  if d.education.len() > 0 {
    cvsection[Education]
    for (i, school) in d.education.enumerate() {
      // The class's \cvevent puts date and location in two half-width boxes,
      // which collide in a column this narrow, so the place goes on its own
      // line below. The LaTeX template does the same thing for the same reason.
      cvevent(school.degree, school.institution, school.dates, "")
      if school.location != "" or school.score != "" {
        block(above: 4pt, below: 0pt, text(size: 9pt, {
          school.location
          if school.location != "" and school.score != "" [, ]
          if school.score != "" [GPA #school.score]
        }))
      }
      if i + 1 < d.education.len() { divider }
    }
  }

  if d.certificates.len() > 0 {
    cvsection[Certifications]
    for cert in d.certificates {
      cvachievement(
        fa-solid("\u{f0a3}"),
        cert.name,
        cert.issuer + if cert.issuer != "" and cert.date != "" { ", " } else { "" } + cert.date,
      )
    }
  }

  if d.awards.len() > 0 {
    cvsection[Honors]
    for award in d.awards {
      cvachievement(fa-solid("\u{f091}"), award.title, award.awarder)
    }
  }

  if d.languages.len() > 0 {
    cvsection[Languages]
    for language in d.languages {
      block(above: 3pt, below: 0pt, grid(
        columns: (auto, 1fr),
        text(size: 10pt, weight: "bold", fill: c-slate, language.language),
        align(right, text(size: 10pt, language.fluency)),
      ))
    }
  }
}

#grid(
  columns: (62%, 1fr),
  column-gutter: 1.1cm,
  wide-column,
  sidebar,
)
