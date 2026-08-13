// One cover-letter layout, restyled per resume template so the two documents
// read as a set.
//
// There is deliberately ONE source file rather than one per resume template.
// What makes a letter look like it belongs with a resume is the typeface, the
// header treatment and the margins, and those are six values, not six documents.
// Six near-identical letter templates would be six places for a fix to be
// applied five times. The values arrive in `d.style`; see
// `services/cover_letter_render.py`.
//
// Content arrives in data.json and is interpolated in code mode, so it is never
// re-parsed as Typst markup. Nothing here escapes anything, and nothing here
// should start: same contract as the resume templates.

#let d = json("data.json")
#let s = d.style

#set page(
  paper: "us-letter",
  margin: (x: s.margin_x * 1in, top: s.margin_y * 1in, bottom: s.margin_y * 1in),
)
#set text(
  font: s.font,
  size: s.base_size * 1pt,
  // Same reasoning as the resume templates: a letter is full of proper nouns and
  // a hyphenated one reads as a typo in the document where a typo costs most.
  hyphenate: false,
)
// Blocks rather than first-line indents, which is what business correspondence
// uses and what a recruiter's eye expects.
#set par(justify: false, leading: 0.62em, spacing: 0.95em)

#let header-align = if s.align == "center" { center } else { left }
#let small = (s.base_size - 1) * 1pt

// ---------------------------------------------------------------------------
// Sender
// ---------------------------------------------------------------------------

#align(header-align, {
  text(
    font: s.name_font,
    size: s.name_size * 1pt,
    tracking: s.tracking * 1pt,
    strong(if s.small_caps { smallcaps(d.sender.name) } else { d.sender.name }),
  )
  linebreak()
  v(2pt)
  // Each item is kept whole with `box`, so a contact line long enough to wrap
  // breaks after a separator rather than inside an email address.
  text(size: small, d.sender.contact.map(c => box(c)).join([ | ]))
})

#if s.rule {
  v(4pt)
  line(length: 100%, stroke: 0.4pt)
}

#v(18pt)

// ---------------------------------------------------------------------------
// Date and recipient
// ---------------------------------------------------------------------------

#text(size: small, d.date)

#v(10pt)

#if d.recipient.len() > 0 {
  for row in d.recipient {
    row
    linebreak()
  }
  v(10pt)
}

#if d.subject != "" {
  strong(d.subject)
  v(10pt)
}

// ---------------------------------------------------------------------------
// Body
// ---------------------------------------------------------------------------

#d.greeting

#for paragraph in d.paragraphs {
  paragraph
  parbreak()
}

#v(6pt)

#d.signoff

#v(20pt)

#d.sender.name
