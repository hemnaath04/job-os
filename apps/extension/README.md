# job.os Autofill

A Manifest V3 browser extension that fills job applications from the user's
verified job.os profile.

It is built around three rules that are enforced by code and covered by tests,
not by convention. Each one exists because the market-leading competitor's
autofill got it wrong in a way that cost real users real interviews.

## The three rules

### 1. It never writes an answer it cannot quote

Every value that reaches a form has to be a `SourcedValue`, and
[`src/core/provenance.ts`](src/core/provenance.ts) is the only module that can
create one. Doing so requires a `ProfileFact` that came back from the API with
`verified === true`, plus the name of the attribute the text came from. There is
no function anywhere in this codebase that turns a question into an answer: no
model call, no template, no summarizer.

Custom employer questions resolve to no canonical field, so they are left blank
and listed in the review panel.
[`src/core/field-key.ts`](src/core/field-key.ts) refuses them by wording, by
control shape (a textarea is prose), and by length.

The specific failure this prevents: a subscriber found the competitor's agent
had answered "Please provide an example of your exceptional ability" with a
fluent paragraph about leading a team project recognised at a national
conference. Their profile mentioned neither thing.

### 2. It never submits

[`src/core/dom-guard.ts`](src/core/dom-guard.ts) is the only module allowed to
write to a page, and `safeClick` refuses anything that looks like a submit
control. `tests/invariant-no-submit.test.ts` scans every other source file for
`.submit(`, `requestSubmit`, `.click(`, and synthetic Enter keys, and that scan
is itself tested against planted violations so it cannot silently rot into a
test that always passes.

Filling never dispatches keyboard events, because a synthetic Enter in a
single-input form submits it.

The other half of this rule is the loud one: required fields that are still
empty after the fill are detected and shown first. An ATS-side recruiter
described the silent failure that makes this necessary. Automated applications
arrive marked complete with required responses missing, so they are rejected
automatically, and the applicant is never told why.

### 3. It shows its work

The review panel lists every field it filled with the fact behind it, every
field it left blank with the reason, and every required field still empty. See
[`src/content/panel.ts`](src/content/panel.ts).

## Permissions

`activeTab`, `scripting`, `storage`, and one host permission for the app's own
origin. No `<all_urls>`, no ATS host permissions, no declared content scripts,
so nothing runs until the user clicks the toolbar button.

Full rationale, including the usability cost of that choice and the policy
sources it was checked against, is in [PERMISSIONS.md](PERMISSIONS.md).

## Auth

The extension holds no secret of any kind. The service worker fetches
`/api/backend/profile/facts?verified=true` on the app origin with
`credentials: "include"`, and Chrome attaches the Clerk session cookie the user
already has. That works because Chrome treats an extension request as same-site
when the extension has host permissions for the target, which is the only reason
the host permission is there.

The profile is kept in a service worker variable with a two minute TTL and is
never written to `chrome.storage`. An MV3 worker is torn down when idle, so the
data's lifetime is bounded by the browser rather than by cleanup code that could
be forgotten. `chrome.storage.local` holds settings only: the app origin and the
per-field demographic opt-ins.

## Adapters

| ATS | Primary signal | Fixture |
| --- | --- | --- |
| Greenhouse | `aria-label`, `<label for>`, stable core ids | real capture |
| Lever | `name` attributes including `urls[LinkedIn]` and `eeo[...]` | real capture |
| Ashby | `_systemfield_*` ids, ARIA wiring | synthesized |
| Workday | `data-automation-id` | synthesized |
| SmartRecruiters | camelCase names, `data-test="field-*"` | synthesized |
| Generic | `autocomplete` and the accessibility tree only | n/a |

Selectors prefer the accessibility tree and semantic attributes over CSS paths,
because class names are the first thing an ATS redesign changes.
`resolveRawLabel` in [`src/core/labels.ts`](src/core/labels.ts) follows roughly
the ARIA accessible-name computation before falling back to anything structural.

Adding an ATS is one file in `src/adapters/` and one line in `registry.ts`. An
adapter can only read the DOM and offer a field-key hint; it never decides what
to fill and never writes, so a new adapter cannot route around the rules above.

## Demographic questions

Off. Every one of them, individually, until the user turns that specific
question on in the popup. A key the user has not opted in never enters the value
map, so the filler cannot reach it even by mistake.

## Build and test

```sh
pnpm --filter @job-os/extension build       # bundles to dist/
pnpm --filter @job-os/extension typecheck
pnpm --filter @job-os/extension test
```

Load `dist/` through `chrome://extensions` in developer mode.

Tests run on `node:test` with jsdom against saved HTML fixtures rather than live
forms, so the suite is deterministic and nobody's application endpoint gets
hammered by CI. `scripts/save-fixture.mjs` captures a new one; it only ever
issues GET requests.

### What the fixtures are

`greenhouse.html` and `lever.html` are real markup, captured from public
postings and reduced to structure. `ashby.html`, `workday.html` and
`smartrecruiters.html` are synthesized from documented selectors, and say so in
their own headers. Ashby renders client side, SmartRecruiters sits behind bot
protection, and Workday puts the form behind account creation. None of those
were worked around. A pass against those three proves the adapter honours the
documented contract, not that a live tenant renders exactly that markup.

## Related change outside this directory

`apps/api/.../profile_import.py` now emits a `contact` fact from JSON Resume
`basics`. That section used to be dropped on import, which meant name, phone,
address and profile links never reached the fact vault and could not be filled
from it. The change is additive (`kind` is a free string, `payload` is JSONB, so
no migration), but existing users need to re-import their resume before contact
autofill has anything to work with.
