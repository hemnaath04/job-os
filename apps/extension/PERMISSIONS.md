# Permission rationale

Every entry in `src/manifest.json` justified, plus the ones deliberately not
requested. Chrome Web Store review asks for exactly this, and the store's
Manifest V3 requirements page is explicit that "the full functionality of an
extension must be easily discernible from its submitted code", so the shape of
the permission set is part of the design rather than an afterthought.

Sources consulted:

- [Additional Requirements for Manifest V3](https://developer.chrome.com/docs/webstore/program-policies/mv3-requirements)
- [Chrome Web Store Program Policies](https://developer.chrome.com/docs/webstore/program-policies/policies)
- [Troubleshooting Chrome Web Store violations](https://developer.chrome.com/docs/webstore/troubleshooting)
- [Storage and cookies](https://developer.chrome.com/docs/extensions/develop/concepts/storage-and-cookies)
- [Cross-origin network requests](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests)

## What is requested

| Entry | Why it is needed | Why it is not wider |
| --- | --- | --- |
| `activeTab` | Read and fill the application form in the tab the user is looking at. | Granted only for the tab that was active when the user clicked the toolbar button, and only until that tab navigates. Nothing is readable before the click. |
| `scripting` | Inject the content script on that click, via `chrome.scripting.executeScript`. | There is no `content_scripts` block in the manifest, so nothing is injected passively. See below. |
| `storage` | Persist settings: the app origin and the per-field demographic opt-ins. | Profile data is never written here. It lives in a service worker variable with a TTL and dies with the worker. |
| `https://jobs.hemnaath.tech/*` | The service worker fetches the signed-in user's profile from the app's own API proxy. | This is the only host permission. It is the app's own origin, not a wildcard and not an ATS. |

## What is deliberately not requested

- **`<all_urls>` or any ATS host permission.** The obvious design declares
  content scripts on `*.greenhouse.io`, `*.lever.co`, `*.myworkdayjobs.com` and
  so on. That grants standing read access to every application page the user
  ever opens, including pages they open and never ask us to touch. `activeTab`
  gets the same job done with access that begins at a click and ends at a
  navigation, which is the narrowest thing that works.
- **`tabs`.** Reading `tab.url` to badge the icon on known ATS domains would
  mean seeing the URL of every tab. Not worth it; the user clicks the button.
- **`cookies`.** The extension never reads a cookie. It makes a credentialed
  fetch and lets Chrome attach the session itself.
- **`declarativeNetRequest`, `webRequest`.** No traffic is inspected or
  modified.
- **`downloads`, `identity`, `offscreen`.** Unused.

## The cost of choosing `activeTab`

Being honest about the tradeoff: `activeTab` means the extension cannot notice
an application page on its own, so there is no badge and no "we can fill this"
hint. The user has to know to click. That is a real usability loss and it was
accepted on purpose, because the alternative is persistent read access to five
ATS vendors' entire domains.

## Remotely hosted code

There is none. No `<script src>` to an external origin, no `eval`, no
`new Function`, no remote template or rule file. The service worker fetches JSON
profile data only, and that data is treated strictly as values to type into
fields, never as logic. This is the requirement the MV3 policy page is most
specific about.

## Content security policy

`script-src 'self'; object-src 'self'; base-uri 'none'`. The default MV3 policy
already forbids remote script; `base-uri 'none'` is added so an injected `<base>`
tag cannot repoint relative URLs on the extension's own pages.
