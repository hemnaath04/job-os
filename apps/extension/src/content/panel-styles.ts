/**
 * Panel styling, as a string injected into a shadow root.
 *
 * A shadow root is not optional here: this panel renders inside pages the
 * extension does not control, and an ATS stylesheet that happens to target
 * `div` or `button` would otherwise wreck it. Shadow DOM also stops our styles
 * leaking the other way onto the form the user is about to submit.
 *
 * Tokens are lifted from apps/web/src/app/globals.css so the panel reads as
 * part of job.os: warm parchment surfaces, Jasmine accent with gold ink for
 * text, mauve and khaki neutrals, and the same easing curves.
 */
export const PANEL_STYLES = `
:host {
  all: initial;
  --bg: #F1EFE3;
  --surface-1: #FFFFFF;
  --surface-2: #F7F5EB;
  --surface-3: #EFECDD;
  --border: #E5E1D1;
  --border-strong: #D8D3C0;
  --text: #2A2530;
  --text-muted: #635C68;
  --text-dim: #837B6E;
  --accent: #FFE787;
  --accent-soft: #FBF1CE;
  --accent-border: #E9C64A;
  --accent-ink: #8A6D12;
  --on-accent: #221F0E;
  --mint-ink: #3E6E4C;
  --amber-ink: #865C15;
  --rose: #C0555F;
  --rose-ink: #A14750;
  --mauve: #7C6C77;
  --radius-card: 1rem;
  --radius-control: 0.7rem;
  --radius-nested: 0.375rem;
  --ease-out: cubic-bezier(0.22, 1, 0.36, 1);
  --dur: 190ms;

  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 2147483647;
  font-family: "Manrope", "Avenir Next", ui-sans-serif, system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}

* { box-sizing: border-box; }

.panel {
  width: 380px;
  max-height: calc(100vh - 32px);
  display: flex;
  flex-direction: column;
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-card);
  box-shadow: 0 1px 2px rgba(60, 50, 40, 0.04), 0 12px 30px -18px rgba(60, 50, 40, 0.22);
  overflow: hidden;
  animation: enter var(--dur) var(--ease-out) both;
}

@keyframes enter {
  from { opacity: 0; transform: translateY(-6px) scale(0.99); }
  to { opacity: 1; transform: none; }
}

@media (prefers-reduced-motion: reduce) {
  .panel { animation: none; }
}

header {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px 14px 12px;
  background: var(--surface-1);
  border-bottom: 1px solid var(--border);
}

.title { flex: 1; min-width: 0; }

h1 {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.subtitle {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-muted);
}

.close {
  appearance: none;
  border: 1px solid var(--border);
  background: var(--surface-2);
  color: var(--text-muted);
  width: 26px;
  height: 26px;
  border-radius: var(--radius-nested);
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  transition: background var(--dur) var(--ease-out), color var(--dur) var(--ease-out);
}
.close:hover { background: var(--surface-3); color: var(--text); }
.close:focus-visible { outline: 2px solid var(--accent-border); outline-offset: 2px; }

.body {
  overflow-y: auto;
  padding: 12px 14px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

section {
  background: var(--surface-1);
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  overflow: hidden;
}

section.alarm {
  border-color: var(--rose);
  box-shadow: 0 0 0 3px rgba(192, 85, 95, 0.12);
}

.section-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.01em;
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
}

section.alarm .section-head {
  background: rgba(192, 85, 95, 0.10);
  color: var(--rose-ink);
  border-bottom-color: rgba(192, 85, 95, 0.25);
}

.count {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: var(--text-dim);
}
section.alarm .count { color: var(--rose-ink); }

.rows { margin: 0; padding: 0; list-style: none; }

.rows li {
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  line-height: 1.45;
}
.rows li:last-child { border-bottom: none; }

.field-label {
  font-weight: 600;
  color: var(--text);
  overflow-wrap: anywhere;
}

.value {
  display: block;
  margin-top: 2px;
  font-family: "Geist Mono", ui-monospace, monospace;
  font-size: 11.5px;
  color: var(--accent-ink);
  background: var(--accent-soft);
  border: 1px solid rgba(233, 198, 74, 0.4);
  border-radius: var(--radius-nested);
  padding: 3px 6px;
  overflow-wrap: anywhere;
}

.source {
  display: block;
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-dim);
  overflow-wrap: anywhere;
}
.source b { font-weight: 600; color: var(--text-muted); }

.reason {
  display: block;
  margin-top: 2px;
  font-size: 11.5px;
  color: var(--text-muted);
}

.tag {
  display: inline-block;
  margin-top: 4px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 2px 6px;
  border-radius: 999px;
  background: var(--surface-3);
  color: var(--text-dim);
}
.tag.blocked { background: rgba(134, 92, 21, 0.12); color: var(--amber-ink); }
.tag.essay { background: rgba(124, 108, 119, 0.14); color: var(--mauve); }

.note {
  margin: 0;
  padding: 10px 12px;
  font-size: 11.5px;
  color: var(--text-muted);
  background: var(--surface-2);
}

footer {
  padding: 11px 14px;
  background: var(--surface-1);
  border-top: 1px solid var(--border);
  font-size: 11.5px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 8px;
}

.pledge {
  font-weight: 600;
  color: var(--accent-ink);
}

.empty {
  padding: 12px;
  font-size: 12px;
  color: var(--text-dim);
}
`;
