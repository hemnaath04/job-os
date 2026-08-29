// The "Co-Op" resume, ported to Typst.
//
// Not a port of anyone else's design: the layout is the user's own personal
// resume, supplied directly as the reference to match. See ATTRIBUTION.md for
// what was copied exactly and what was generalised into the template contract.
//
// Content arrives in data.json and is interpolated in code mode, which means it
// is never re-parsed as Typst markup. Nothing here escapes anything, and nothing
// here should start: see the note at the top of typst_render.py.

#let d = json("data.json")

// \usepackage[top=0.45in, bottom=0.4in, left=0.6in, right=0.6in]{geometry},
// which is tighter than the half-inch-all-round the other single-column
// templates use, and \pagestyle{empty}, so there is no page number.
#set page(paper: "us-letter", margin: (top: 0.45in, bottom: 0.4in, x: 0.6in))

// mathptmx puts the article class's Computer Modern aside for Times. What it
// actually loads is URW Nimbus Roman No9 L: the Tectonic render embeds
// NimbusRomNo9L-Regu, -Medi, -ReguItal and -MediItal. TeX Gyre Termes is that
// same URW face with its character set extended, so this is the design the
// LaTeX page is set in rather than a Times lookalike standing in for it.
#set text(font: "TeX Gyre Termes", size: 11pt, hyphenate: false)
// 13.5pt between baselines, which is single spacing at 11pt and what the
// Tectonic render measures. \parskip is 1pt, and \parindent is zero.
#set par(justify: false, leading: 0.575em, spacing: 1pt, first-line-indent: 0pt)

#let maybe-link(url, body) = if url == "" { body } else { link(url, body) }

// \titleformat{\section}{\large\bfseries}{}{0em}{\MakeUppercase}[\vspace{-2pt}\titlerule]
// with \titlespacing*{\section}{0pt}{5pt}{3pt}. \large is 12pt at an 11pt base
// and \titlerule is 0.4pt, both confirmed against the rendered page.
#let section(title) = block(width: 100%, above: 11pt, below: 3pt, {
  text(size: 12pt, weight: "bold", upper(title))
  // The rule sits 2.7pt under the heading's baseline in the Tectonic render.
  // The \vspace{-2pt} the LaTeX \titleformat writes is cancelling titlesec's
  // own gap, which Typst does not add, so copying the negative number put the
  // line through the middle of the letters.
  v(1.2pt)
  line(length: 100%, stroke: 0.4pt)
})

// The reference file's own \entry macro: two minipages, 0.72\textwidth and
// 0.27\textwidth, with \hfill between them. The fixed split is the point of it,
// and not a 1fr column that shrinks to fit: it is what makes a long date range
// wrap inside the right column instead of squeezing the title on the left.
//
// The fourth slot is empty in every call below, exactly as it is in the LaTeX
// original, and kept for the same reason: a section that needs a second
// right-hand line later should not need a new macro.
#let entry(title, right-top, subtitle, right-bottom) = block(
  width: 100%,
  above: 8pt,
  below: 0pt,
  breakable: false,
  grid(
    columns: (72%, 1fr, 27%),
    align: (left + top, left + top, right + top),
    {
      strong(title)
      if subtitle != "" {
        linebreak()
        emph(subtitle)
      }
    },
    [],
    {
      right-top
      if right-bottom != "" {
        linebreak()
        right-bottom
      }
    },
  ),
)

// \setlist[bullets]{leftmargin=1.4em, label=$\bullet$, itemsep=1pt, topsep=1pt,
// parsep=0pt, after=\vspace{1pt}}. Bullet text is \normalsize here, unlike
// jakes and sb2nov, which drop it a point.
#let bullets(items) = if items.len() > 0 {
  block(
    above: 8.2pt,
    below: 12.3pt,
    list(indent: 0.55em, body-indent: 0.45em, spacing: 7.2pt, marker: [•], ..items),
  )
}

// \skillrow{#1}{#2}: one bold-italic label per line rather than a bulleted
// block, then \vspace{2pt}.
#let skillrow(label, body) = block(above: 9.2pt, below: 0pt, {
  strong(emph(label + ":"))
  [ ]
  body
})

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

#align(center, {
  text(size: 14.4pt, weight: "bold", upper(d.name))
  linebreak()
  v(2pt)
  // Each item carries its own separator inside the box, which is what
  // \mbox{item $|$} does: a contact line long enough to wrap leaves the bar at
  // the end of the first line rather than starting the second with one.
  text(size: 10pt, {
    for (i, item) in d.contact.enumerate() {
      box({
        maybe-link(item.url, item.text)
        if i + 1 < d.contact.len() { [ |] }
      })
      if i + 1 < d.contact.len() { [ ] }
    }
  })
})

// The space a `center` environment leaves after itself, which the first section
// rule sits 25.6pt below in the Tectonic render and 17.4 below without this.
#v(9pt)

// ---------------------------------------------------------------------------
// Body
// ---------------------------------------------------------------------------

// Education leads, which is the order a co-op posting asks an early-career
// applicant for, and skills come before experience for the same reason.
#if d.education.len() > 0 {
  section[Education]
  for school in d.education {
    entry(
      school.institution + if school.location != "" { ", " + school.location } else { "" },
      school.dates,
      school.degree,
      "",
    )
    if school.bullets.len() > 0 {
      block(above: 4.3pt, below: 0pt, "Relevant coursework: " + school.bullets.join("; "))
    }
    v(1.6pt)
  }
}

#if d.skills.len() > 0 or d.certificates.len() > 0 {
  section[Technical Skills]
  for group in d.skills {
    skillrow(group.name, group.keywords_line)
  }
  // Certificates are one more labelled row rather than a section of their own,
  // as they are in the LaTeX original.
  if d.certificates.len() > 0 {
    skillrow("Certifications", d.certificates.map(cert => {
      cert.name
      if cert.issuer != "" { ", " + cert.issuer }
      if cert.date != "" { " (" + cert.date + ")" }
    }).join("; "))
  }
}

#if d.work.len() > 0 {
  section[Professional Experience]
  for job in d.work {
    entry(
      job.company + if job.location != "" { ", " + job.location } else { "" },
      job.dates,
      job.position,
      "",
    )
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
    entry(
      maybe-link(project.url, project.name),
      project.dates,
      project.meta_line,
      "",
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
