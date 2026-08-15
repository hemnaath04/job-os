/**
 * Small, real-styled recreations of each page's most distinctive UI moment.
 *
 * Same reasoning as the landing page's MatchPreview: marked up in the app's
 * own tokens rather than exported as screenshots, so these stay sharp on any
 * display, weigh nothing, read to a screen reader, and cannot drift out of
 * date the way a PNG of last month's UI does.
 */

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)]/80 p-2 shadow-2xl backdrop-blur-xl">
      <div className="rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-5 text-left">
        {children}
      </div>
    </div>
  );
}

export function DashboardMock() {
  const stats = [
    { label: "Applications", value: "24", delta: "+3 this week" },
    { label: "Response rate", value: "38%", delta: null },
    { label: "Interviews", value: "6", delta: "25% conv." },
    { label: "Offers", value: "1", delta: null },
  ];
  return (
    <Frame>
      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        {stats.map((s) => (
          <div
            key={s.label}
            className="rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] p-3"
          >
            <p className="text-[10px] uppercase tracking-wide text-[color:var(--color-text-dim)]">
              {s.label}
            </p>
            <p className="mt-1 text-xl font-medium tabular-nums">{s.value}</p>
            {s.delta && (
              <p className="mt-0.5 text-[10px] text-[color:var(--color-mint-ink)]">{s.delta}</p>
            )}
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-end gap-1.5 rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] p-3">
        {[6, 10, 4, 14, 8, 16, 11].map((h, i) => (
          <div
            key={i}
            className="w-full rounded-t bg-gradient-brand"
            style={{ height: `${h * 3}px` }}
          />
        ))}
      </div>
    </Frame>
  );
}

export function ApplicationsMock() {
  const cols: { label: string; cards: string[] }[] = [
    { label: "Applied", cards: ["Stripe", "Datadog"] },
    { label: "Interview", cards: ["Anthropic"] },
    { label: "Offer", cards: ["Vercel"] },
  ];
  return (
    <Frame>
      <div className="grid grid-cols-3 gap-2.5">
        {cols.map((c) => (
          <div key={c.label} className="min-w-0">
            <p className="mb-1.5 truncate text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-text-dim)]">
              {c.label}
            </p>
            <div className="flex flex-col gap-1.5">
              {c.cards.map((name) => (
                <div
                  key={name}
                  className="truncate rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] px-2 py-1.5 text-[11px]"
                >
                  {name}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Frame>
  );
}

export function JobsMock() {
  return (
    <Frame>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">Backend Engineer, New Grad</p>
          <p className="mt-0.5 truncate text-xs text-[color:var(--color-text-muted)]">
            Remote &middot; posted 2 days ago
          </p>
        </div>
        <span className="shrink-0 rounded-full border border-[color:var(--color-mint-ink)]/35 bg-[color:var(--color-mint-ink)]/15 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-[color:var(--color-mint-ink)]">
          71% fit
        </span>
      </div>
      <div className="mt-3 flex gap-2">
        <span className="rounded-full bg-gradient-brand px-3 py-1 text-[11px] font-semibold text-[color:var(--color-on-accent)]">
          Import
        </span>
        <span className="rounded-full border border-[color:var(--color-border-strong)] px-3 py-1 text-[11px]">
          Tailor →
        </span>
      </div>
    </Frame>
  );
}

export function TailorMock() {
  const steps = ["Reading the posting", "Matching your facts", "Writing", "Scoring"];
  return (
    <Frame>
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium">Tailoring…</span>
        <span className="text-[color:var(--color-text-dim)]">est.</span>
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-[color:var(--color-surface-1)]">
        <div className="h-full w-2/3 rounded-full bg-gradient-brand" />
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {steps.map((s, i) => (
          <span
            key={s}
            className={`rounded-full px-2 py-0.5 text-[10px] ${
              i < 2
                ? "bg-[color:var(--color-mint-ink)]/15 text-[color:var(--color-mint-ink)]"
                : "bg-[color:var(--color-surface-1)] text-[color:var(--color-text-dim)]"
            }`}
          >
            {s}
          </span>
        ))}
      </div>
    </Frame>
  );
}

export function InterviewMock() {
  return (
    <Frame>
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">Readiness</p>
        <span className="rounded-full border border-[color:var(--color-mint-ink)]/35 bg-[color:var(--color-mint-ink)]/15 px-2 py-0.5 text-[11px] font-semibold text-[color:var(--color-mint-ink)]">
          72 &middot; Mixed
        </span>
      </div>
      <div className="mt-3 rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] p-2.5 text-[11px]">
        <p className="text-[color:var(--color-text-dim)]">Resume probe</p>
        <p className="mt-1">&ldquo;Walk me through the caching layer on your bullet 2.&rdquo;</p>
      </div>
    </Frame>
  );
}

export function ResumesMock() {
  return (
    <Frame>
      <div className="flex items-center justify-between rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] px-3 py-2">
        <span className="text-xs font-medium">Master resume</span>
        <span className="rounded-full bg-gradient-brand px-2 py-0.5 text-[10px] font-semibold text-[color:var(--color-on-accent)]">
          protected
        </span>
      </div>
      <div className="mt-2 space-y-1.5">
        {["v3 · tailored · ATS 88", "v2 · tailored · ATS 74"].map((v) => (
          <div
            key={v}
            className="rounded-md border border-[color:var(--color-border)] px-3 py-1.5 text-[11px] text-[color:var(--color-text-muted)]"
          >
            {v}
          </div>
        ))}
      </div>
    </Frame>
  );
}

export function CoverLettersMock() {
  return (
    <Frame>
      <p className="text-[11px] leading-relaxed">
        I built the caching layer that cut p95 latency 40%
        <sup className="mx-0.5 text-[color:var(--color-mint-ink)]">[bullet 4]</sup>, and led the
        migration off the legacy queue.
      </p>
      <div className="mt-3 rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] p-2 text-[10px] text-[color:var(--color-text-dim)]">
        Sentences that did not print: 1 removed (no evidence)
      </div>
    </Frame>
  );
}

export function ProfileMock() {
  const facts = ["Backend Engineer @ Acme", "Distributed Systems", "PostgreSQL", "Go"];
  return (
    <Frame>
      <div className="flex flex-wrap gap-1.5">
        {facts.map((f) => (
          <span
            key={f}
            className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] px-2.5 py-1 text-[11px]"
          >
            <span className="size-1.5 rounded-full bg-[color:var(--color-mint-ink)]" />
            {f}
          </span>
        ))}
      </div>
    </Frame>
  );
}

export function CalendarMock() {
  const rows = [
    { label: "Overdue", item: "Follow up · Datadog" },
    { label: "Today", item: "Phone screen · Anthropic" },
    { label: "This week", item: "Follow up · Stripe" },
  ];
  return (
    <Frame>
      <div className="space-y-2">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center gap-2 text-[11px]">
            <span className="w-16 shrink-0 uppercase tracking-wide text-[color:var(--color-text-dim)]">
              {r.label}
            </span>
            <span className="truncate rounded-md border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] px-2 py-1">
              {r.item}
            </span>
          </div>
        ))}
      </div>
    </Frame>
  );
}

export function SettingsMock() {
  return (
    <Frame>
      <div className="space-y-2 text-[11px]">
        <div className="flex items-center justify-between">
          <span className="text-[color:var(--color-text-muted)]">Work authorization</span>
          <span>US citizen</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-[color:var(--color-text-muted)]">Target roles</span>
          <span>SWE, Backend</span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-[color:var(--color-text-muted)]">Salary floor</span>
          <span>$110,000</span>
        </div>
      </div>
    </Frame>
  );
}
