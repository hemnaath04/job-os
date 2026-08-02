// Deedy, One Page Two Column Resume, ported to Typst.
//
// Original author: Debarghya Das, https://github.com/deedydas/Deedy-Resume,
// Apache-2.0. This follows the repository's OpenFonts variant. Lato and Raleway
// are SIL OFL 1.1 and are used from the copies vendored beside the LaTeX
// template, along with the OFL text upstream omits. See ATTRIBUTION.md.
//
// Content arrives in data.json and is interpolated in code mode, which means it
// is never re-parsed as Typst markup. Nothing here escapes anything, and nothing
// here should start: see the note at the top of typst_render.py.

#let d = json("data.json")

// Every value below is measured off deedy-resume-openfont.cls rather than
// guessed: the margins, the four colours, the point sizes and the 8pt gap
// between sections are the class's own.
#let c-date = rgb("#666666")
#let c-primary = rgb("#2b2b2b")
#let c-headings = rgb("#6a6a6a")
#let c-subheadings = rgb("#333333")

#set page(paper: "us-letter", margin: (x: 1.25cm, y: 0.75cm))
#set text(font: "Lato", weight: 300, size: 10pt, fill: c-primary, hyphenate: false)
#set par(justify: false, leading: 0.62em, spacing: 0.62em)
#set list(indent: 0.6em, body-indent: 0.4em, spacing: 0.45em, marker: [•])

#let maybe-link(url, body) = if url == "" { body } else { link(url, body) }

#let sectionsep = v(8pt)

// The class uppercases section titles and sets them in small caps at the same
// time, which lands on plain capitals.
#let section(title) = block(
  above: 4pt,
  below: 7pt,
  text(font: "Lato", weight: 300, size: 16pt, fill: c-headings, upper(title)),
)

#let subsection(body) = block(
  above: 0pt,
  below: 3pt,
  {
    set par(leading: 0.1em)
    text(font: "Lato", weight: 700, size: 12pt, fill: c-subheadings, upper(body))
  },
)

#let descript(body) = block(
  above: 0pt,
  below: 3pt,
  {
    set par(leading: 0.2em)
    text(font: "Raleway", weight: 500, size: 11pt, fill: c-subheadings, smallcaps(body))
  },
)

#let location(body) = block(
  above: 0pt,
  below: 3pt,
  {
    set par(leading: 0.2em)
    text(font: "Raleway", weight: 500, size: 10pt, fill: c-headings, body)
  },
)

// ---------------------------------------------------------------------------
// Heading
// ---------------------------------------------------------------------------

// "Last Updated on 2nd August 2026", in the class's UK long form. The ordinal
// is spelled out here because Typst's date formatter has no ordinal specifier.
#let ordinal(n) = {
  let suffix = if n >= 11 and n <= 13 { "th" } else if calc.rem(n, 10) == 1 { "st" } else if (
    calc.rem(n, 10) == 2
  ) { "nd" } else if calc.rem(n, 10) == 3 { "rd" } else { "th" }
  str(n) + suffix
}

#let today = datetime.today()

// On its own line rather than absolutely positioned the way the class does it.
// The class drops this into a textblock at a fixed point, which is fine for a
// short name and collides with a long one: "Hemnaath Balasubramani" at 40pt
// reaches the right margin at exactly this height. A real name found that.
#block(
  above: 0pt,
  below: 0pt,
  align(right, text(font: "Raleway", weight: 200, size: 8pt, fill: c-date)[
    Last Updated on #ordinal(today.day()) #today.display("[month repr:long] [year]")
  ]),
)

#align(center, {
  text(font: "Lato", weight: 250, size: 40pt, d.first_name + " ")
  text(font: "Lato", weight: 300, size: 40pt, d.last_name)
  v(5pt, weak: true)
  block(text(
    font: "Raleway",
    weight: 500,
    size: 11pt,
    fill: c-headings,
    d.contact.map(c => box(maybe-link(c.url, c.text))).join([ | ]),
  ))
})

#v(3pt)
#line(length: 100%, stroke: 0.4pt + c-headings)
#v(5pt)

// ---------------------------------------------------------------------------
// The two columns
// ---------------------------------------------------------------------------
//
// A third and two thirds, as the class's two minipages are. The known upstream
// behaviour is kept as well: a column longer than the page runs onto a second
// one rather than being balanced.

#let left-column = {
  if d.education.len() > 0 {
    section[Education]
    for school in d.education {
      subsection(school.institution)
      if school.degree != "" { descript(school.degree) }
      location(school.dates + if school.location != "" { " | " + school.location } else { "" })
      if school.score != "" { location("GPA: " + school.score) }
      sectionsep
    }
  }

  if d.profiles.len() > 0 {
    section[Links]
    for profile in d.profiles {
      let name = if profile.network != "" { profile.network } else { "Web" }
      let handle = if profile.username != "" { profile.username } else { profile.label }
      block(above: 0pt, below: 3pt, {
        text(fill: c-headings, name + "://")
        h(0.4em)
        text(weight: 700, maybe-link(profile.url, handle))
      })
    }
    sectionsep
  }

  if d.skills.len() > 0 {
    section[Skills]
    for group in d.skills {
      subsection(group.name)
      block(above: 0pt, below: 3pt, group.keywords.join([ • ]))
      sectionsep
    }
  }

  if d.certificates.len() > 0 {
    section[Certifications]
    for cert in d.certificates {
      block(above: 0pt, below: 2pt, text(weight: 700, cert.name))
      if cert.issuer != "" or cert.date != "" {
        location(cert.issuer + if cert.issuer != "" and cert.date != "" { " | " } else { "" } + cert.date)
      }
    }
    sectionsep
  }

  if d.languages.len() > 0 {
    section[Languages]
    for language in d.languages {
      block(above: 0pt, below: 3pt, {
        text(weight: 700, language.language)
        if language.fluency != "" { [ • #language.fluency] }
      })
    }
    sectionsep
  }
}

#let right-column = {
  if d.summary != "" {
    section[Summary]
    d.summary
    sectionsep
  }

  if d.work.len() > 0 {
    section[Experience]
    for job in d.work {
      block(above: 0pt, below: 3pt, {
        text(font: "Lato", weight: 700, size: 12pt, fill: c-subheadings, upper(job.company))
        if job.position != "" {
          h(0.4em)
          text(font: "Raleway", weight: 500, size: 11pt, fill: c-subheadings, smallcaps("| " + job.position))
        }
      })
      location(job.dates + if job.location != "" { " | " + job.location } else { "" })
      if job.bullets.len() > 0 {
        list(..job.bullets)
      } else if job.summary != "" {
        job.summary
      }
      sectionsep
    }
  }

  if d.projects.len() > 0 {
    section[Projects]
    for project in d.projects {
      block(above: 0pt, below: 3pt, {
        text(
          font: "Lato",
          weight: 700,
          size: 12pt,
          fill: c-subheadings,
          maybe-link(project.url, upper(project.name)),
        )
        if project.keywords_line != "" {
          h(0.4em)
          text(
            font: "Raleway",
            weight: 500,
            size: 11pt,
            fill: c-subheadings,
            smallcaps("| " + project.keywords_line),
          )
        }
      })
      if project.dates != "" { location(project.dates) }
      if project.bullets.len() > 0 {
        // The description leads the list when there is one, which is where the
        // LaTeX version puts it too.
        list(..(if project.description != "" { (project.description,) } else { () } + project.bullets))
      } else if project.description != "" {
        project.description
      }
      sectionsep
    }
  }

  if d.awards.len() > 0 {
    section[Honors]
    for award in d.awards {
      block(above: 0pt, below: 3pt, {
        text(font: "Lato", weight: 700, size: 12pt, fill: c-subheadings, upper(award.title))
        if award.awarder != "" {
          h(0.4em)
          text(font: "Raleway", weight: 500, size: 11pt, fill: c-subheadings, smallcaps("| " + award.awarder))
        }
      })
      sectionsep
    }
  }
}

#grid(
  columns: (33%, 66%),
  column-gutter: 1%,
  left-column,
  right-column,
)
