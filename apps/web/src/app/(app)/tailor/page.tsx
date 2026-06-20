"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  Building2,
  CheckCircle2,
  Download,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type {
  GapQuestion,
  JsonResume,
  ProfileFact,
  ProvenanceEntry,
  Resume,
  TailorResponse,
} from "@/lib/types";

export default function TailorPage() {
  return (
    <Suspense fallback={<PageShell loading />}>
      <TailorInner />
    </Suspense>
  );
}

function TailorInner() {
  const params = useSearchParams();
  const initialJobId = params.get("job_id") ?? "";

  const [jobId, setJobId] = useState<string>(initialJobId);
  const [resumeId, setResumeId] = useState<string>("");
  const [result, setResult] = useState<TailorResponse | null>(null);

  const { data: resumes = [], isLoading: resumesLoading } = useQuery({
    queryKey: ["resumes"],
    queryFn: () => api.listResumes(),
  });
  const { data: jobs = [], isLoading: jobsLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.listJobs(),
  });

  // Auto-pick first non-master resume when none chosen.
  const candidateResumes = useMemo(() => resumes.filter((r) => !r.is_master), [resumes]);
  if (!resumeId && candidateResumes.length > 0) {
    setResumeId(candidateResumes[0].id);
  }

  const tailor = useMutation({
    mutationFn: () => api.tailorResume(resumeId, jobId),
    onSuccess: (data) => {
      setResult(data);
      toast.success("Tailored resume ready");
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const job = jobs.find((j) => j.id === jobId);
  const targetResume = resumes.find((r) => r.id === resumeId);
  const masterResume = resumes.find((r) => r.is_master);
  const hasMaster = !!masterResume;
  const canRun = !!jobId && !!resumeId && hasMaster && !tailor.isPending;

  if (result && targetResume) {
    return (
      <ResultView
        result={result}
        resume={targetResume}
        jobTitle={job?.title ?? "—"}
        companyName={job?.company?.name ?? null}
        onReset={() => setResult(null)}
      />
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-8 py-6">
      <header>
        <h1 className="text-2xl font-medium tracking-tight">Tailor a resume</h1>
        <p className="text-sm text-[color:var(--color-text-muted)]">
          Pick a job and a target template. The agent loads your master, your
          verified facts, and the JD — then rewrites bullets without inventing
          anything. Anything missing comes back as a gap question.
        </p>
      </header>

      {!hasMaster && !resumesLoading && (
        <div className="glass mt-6 flex items-start gap-3 rounded-[var(--radius-card)] border border-amber-400/30 p-4">
          <AlertCircle className="mt-0.5 size-4 text-amber-400" />
          <div className="text-sm">
            <div className="font-medium">No master resume yet</div>
            <p className="text-[color:var(--color-text-muted)]">
              Upload your master PDF on the{" "}
              <Link href="/profile" className="text-[color:var(--color-violet)] underline">
                Profile
              </Link>{" "}
              page first — tailoring always starts from a clean master baseline.
            </p>
          </div>
        </div>
      )}

      <div className="mt-8 space-y-6">
        <Field
          label="Job"
          help="The JD to tailor against. Add jobs from Applications."
        >
          <select
            value={jobId}
            onChange={(e) => setJobId(e.target.value)}
            disabled={jobsLoading}
            className="glass w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-white/[0.03] px-3 py-2 text-sm outline-none focus:border-[#7C5CFF]/60"
          >
            <option value="">— pick a job —</option>
            {jobs.map((j) => (
              <option key={j.id} value={j.id}>
                {j.title}
                {j.company?.name ? ` · ${j.company.name}` : ""}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label="Target resume template"
          help="Where the new tailored version gets saved. Pick a role-specific resume — not Master."
        >
          <select
            value={resumeId}
            onChange={(e) => setResumeId(e.target.value)}
            disabled={resumesLoading}
            className="glass w-full rounded-[var(--radius-input,12px)] border border-white/10 bg-white/[0.03] px-3 py-2 text-sm outline-none focus:border-[#7C5CFF]/60"
          >
            <option value="">— pick a template —</option>
            {candidateResumes.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
                {r.base_role ? ` · ${r.base_role}` : ""}
              </option>
            ))}
          </select>
          {candidateResumes.length === 0 && !resumesLoading && (
            <p className="mt-1.5 text-xs text-[color:var(--color-text-dim)]">
              You only have a Master resume. Create a role-specific resume (e.g.
              &ldquo;SWE&rdquo;, &ldquo;ML&rdquo;) from{" "}
              <Link href="/resumes" className="text-[color:var(--color-violet)] underline">
                Resumes
              </Link>
              .
            </p>
          )}
        </Field>

        <button
          onClick={() => tailor.mutate()}
          disabled={!canRun}
          className="inline-flex items-center gap-2 rounded-full bg-[#7C5CFF] px-5 py-2 text-sm font-medium text-white shadow-[0_0_30px_-8px_#7C5CFF] enabled:hover:bg-[#8C6CFF] disabled:cursor-not-allowed disabled:opacity-40"
        >
          {tailor.isPending ? (
            <>
              <Loader2 className="size-4 animate-spin" /> Tailoring…
            </>
          ) : (
            <>
              <Sparkles className="size-4" /> Tailor resume
            </>
          )}
        </button>

        {tailor.isPending && (
          <p className="text-xs text-[color:var(--color-text-dim)]">
            One Opus pass through Manifest — usually 20–60s.
          </p>
        )}
      </div>
    </div>
  );
}

function ResultView({
  result,
  resume,
  jobTitle,
  companyName,
  onReset,
}: {
  result: TailorResponse;
  resume: Resume;
  jobTitle: string;
  companyName: string | null;
  onReset: () => void;
}) {
  const qc = useQueryClient();
  const downloadUrl = api.downloadVersionUrl(result.resume_id, result.id);

  // Pull all facts so we can show the ORIGINAL bullet text under each rewritten
  // bullet — that's the diff signal users care about for the no-hallucination
  // contract.
  const { data: facts = [] } = useQuery({
    queryKey: ["facts"],
    queryFn: () => api.listFacts(),
  });
  const originalBulletText = useMemo(() => {
    const map: Record<string, string> = {};
    for (const f of facts) {
      for (const b of f.bullets) map[b.id] = b.text;
    }
    return map;
  }, [facts]);

  const approve = useMutation({
    mutationFn: () => api.approveVersion(result.resume_id, result.id),
    onSuccess: () => {
      toast.success("Version approved");
      qc.invalidateQueries({ queryKey: ["versions", result.resume_id] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <div className="mx-auto max-w-4xl px-8 py-6">
      <button
        onClick={onReset}
        className="inline-flex items-center gap-1.5 text-xs text-[color:var(--color-text-muted)] hover:text-white"
      >
        <ArrowLeft className="size-3" /> Tailor another
      </button>

      <header className="mt-3 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-medium tracking-tight">
            {resume.name}{" "}
            <span className="text-[color:var(--color-text-dim)]">·</span>{" "}
            <span className="font-normal">{jobTitle}</span>
          </h1>
          <div className="mt-1 flex items-center gap-2 text-sm text-[color:var(--color-text-muted)]">
            {companyName && (
              <span className="inline-flex items-center gap-1">
                <Building2 className="size-3.5" /> {companyName}
              </span>
            )}
            <AtsBadge score={result.ats_score} />
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onReset}
            className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs hover:bg-white/[0.06]"
          >
            <RefreshCw className="size-3" /> Re-tailor
          </button>
          <a
            href={downloadUrl}
            download={`resume_${result.id.slice(0, 8)}.pdf`}
            className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs hover:bg-white/[0.06]"
          >
            <Download className="size-3" /> Download PDF
          </a>
          <button
            onClick={() => approve.mutate()}
            disabled={result.approved_by_user || approve.isPending}
            className="inline-flex items-center gap-1.5 rounded-full bg-[#7C5CFF] px-4 py-1.5 text-xs font-medium text-white shadow-[0_0_30px_-8px_#7C5CFF] enabled:hover:bg-[#8C6CFF] disabled:opacity-50"
          >
            <CheckCircle2 className="size-3" />
            {result.approved_by_user ? "Approved" : approve.isPending ? "…" : "Approve"}
          </button>
        </div>
      </header>

      {result.agent_note && (
        <div className="glass mt-6 rounded-[var(--radius-card)] border border-[#7C5CFF]/20 p-4">
          <div className="flex items-start gap-3">
            <Sparkles className="mt-0.5 size-4 text-[color:var(--color-violet)]" />
            <p className="text-sm leading-relaxed text-[color:var(--color-text-muted)]">
              {result.agent_note}
            </p>
          </div>
        </div>
      )}

      <AtsPanel
        matched={result.ats_report?.matched ?? []}
        missing={result.ats_report?.missing ?? []}
      />

      {result.gap_questions.length > 0 && (
        <GapPanel gaps={result.gap_questions} facts={facts} />
      )}

      <ResumeRender
        json={result.json_resume}
        provenance={result.provenance}
        originalBulletText={originalBulletText}
      />
    </div>
  );
}

// ---- Helper components ------------------------------------------------------

function PageShell({ loading = false }: { loading?: boolean }) {
  return (
    <div className="mx-auto max-w-3xl px-8 py-6">
      <header>
        <h1 className="text-2xl font-medium tracking-tight">Tailor a resume</h1>
        {loading && (
          <p className="text-sm text-[color:var(--color-text-muted)]">loading…</p>
        )}
      </header>
    </div>
  );
}

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="text-sm font-medium">{label}</label>
      {help && (
        <p className="mt-0.5 text-xs text-[color:var(--color-text-dim)]">{help}</p>
      )}
      <div className="mt-2">{children}</div>
    </div>
  );
}

function AtsBadge({ score }: { score: string | null }) {
  if (score === null || score === undefined) return null;
  const numeric = Number(score);
  const color =
    numeric >= 75
      ? "text-[color:var(--color-mint)] bg-[color:var(--color-mint)]/10"
      : numeric >= 50
        ? "text-amber-300 bg-amber-400/10"
        : "text-rose-300 bg-rose-400/10";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${color}`}
    >
      ATS {numeric.toFixed(1)}
    </span>
  );
}

function AtsPanel({ matched, missing }: { matched: string[]; missing: string[] }) {
  if (matched.length === 0 && missing.length === 0) return null;
  return (
    <div className="glass mt-4 rounded-[var(--radius-card)] p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-[color:var(--color-text-dim)]">
        ATS keyword coverage
      </div>
      <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
        <KeywordGroup label="Matched" tone="mint" items={matched} />
        <KeywordGroup label="Missing" tone="rose" items={missing} />
      </div>
    </div>
  );
}

function KeywordGroup({
  label,
  tone,
  items,
}: {
  label: string;
  tone: "mint" | "rose";
  items: string[];
}) {
  const colorClass =
    tone === "mint"
      ? "bg-[color:var(--color-mint)]/10 text-[color:var(--color-mint)]"
      : "bg-rose-400/10 text-rose-300";
  return (
    <div>
      <div className="text-xs text-[color:var(--color-text-muted)]">
        {label} · {items.length}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {items.length === 0 && (
          <span className="text-xs text-[color:var(--color-text-dim)]">—</span>
        )}
        {items.map((k) => (
          <span
            key={k}
            className={`rounded-full px-2 py-0.5 text-[11px] ${colorClass}`}
          >
            {k}
          </span>
        ))}
      </div>
    </div>
  );
}

function GapPanel({ gaps, facts }: { gaps: GapQuestion[]; facts: ProfileFact[] }) {
  const factById = useMemo(() => {
    const m: Record<string, ProfileFact> = {};
    for (const f of facts) m[f.id] = f;
    return m;
  }, [facts]);

  return (
    <div className="glass mt-4 rounded-[var(--radius-card)] border border-amber-400/25 p-4">
      <div className="flex items-center gap-2">
        <AlertCircle className="size-4 text-amber-400" />
        <div className="text-sm font-medium">
          Gaps the agent surfaced — {gaps.length} requirement
          {gaps.length === 1 ? "" : "s"} the JD asks for that your profile
          doesn&apos;t cover
        </div>
      </div>
      <ul className="mt-3 space-y-3">
        {gaps.map((g, i) => (
          <li key={i} className="rounded-[var(--radius-card)] bg-white/[0.02] p-3">
            <div className="text-sm font-medium">{g.requirement}</div>
            <div className="mt-1 text-xs text-[color:var(--color-text-muted)]">
              {g.why_no_match}
            </div>
            {g.suggested_fact_ids.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {g.suggested_fact_ids.map((fid) => {
                  const f = factById[fid];
                  if (!f) return null;
                  return (
                    <span
                      key={fid}
                      className="rounded-full bg-white/[0.04] px-2 py-0.5 text-[11px] text-[color:var(--color-text-muted)]"
                    >
                      nearest: {f.title}
                    </span>
                  );
                })}
              </div>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-[color:var(--color-text-dim)]">
        Add a verified fact on the{" "}
        <Link href="/profile" className="text-[color:var(--color-violet)] underline">
          Profile
        </Link>{" "}
        page, then re-tailor.
      </p>
    </div>
  );
}

// ---- Resume render ----------------------------------------------------------

function ResumeRender({
  json,
  provenance,
  originalBulletText,
}: {
  json: JsonResume;
  provenance: ProvenanceEntry[];
  originalBulletText: Record<string, string>;
}) {
  // Index provenance by (section, text) to look up fact_bullet_id quickly when
  // walking the rendered highlights.
  const provIndex = useMemo(() => {
    const m: Record<string, ProvenanceEntry> = {};
    for (const p of provenance) m[`${p.section}::${p.text}`] = p;
    return m;
  }, [provenance]);

  return (
    <article className="glass mt-6 rounded-[var(--radius-card)] p-8">
      {json.basics && (
        <header className="border-b border-white/[0.06] pb-4">
          {json.basics.name && (
            <h2 className="text-xl font-semibold tracking-tight">
              {json.basics.name}
            </h2>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-[color:var(--color-text-muted)]">
            {json.basics.label && <span>{json.basics.label}</span>}
            {json.basics.email && <span>· {json.basics.email}</span>}
            {json.basics.phone && <span>· {json.basics.phone}</span>}
            {json.basics.location?.city && (
              <span>
                · {json.basics.location.city}
                {json.basics.location.region ? `, ${json.basics.location.region}` : ""}
              </span>
            )}
          </div>
          {json.basics.summary && (
            <p className="mt-3 text-sm leading-relaxed text-[color:var(--color-text-muted)]">
              {json.basics.summary}
            </p>
          )}
        </header>
      )}

      <Section title="Experience">
        {(json.work ?? []).map((w, i) => (
          <Entry
            key={i}
            primary={w.position ?? ""}
            secondary={w.name ?? ""}
            dates={dateRange(w.startDate, w.endDate)}
            location={w.location ?? null}
            summary={w.summary ?? null}
            highlights={w.highlights ?? []}
            section="work"
            provIndex={provIndex}
            originalBulletText={originalBulletText}
          />
        ))}
      </Section>

      <Section title="Projects">
        {(json.projects ?? []).map((p, i) => (
          <Entry
            key={i}
            primary={p.name ?? ""}
            secondary={p.entity ?? null}
            dates={dateRange(p.startDate, p.endDate)}
            location={null}
            summary={p.description ?? null}
            highlights={p.highlights ?? []}
            section="projects"
            provIndex={provIndex}
            originalBulletText={originalBulletText}
          />
        ))}
      </Section>

      <Section title="Education">
        {(json.education ?? []).map((e, i) => (
          <div key={i} className="mt-3 first:mt-0">
            <div className="flex items-baseline justify-between">
              <div className="text-sm font-medium">{e.institution}</div>
              <div className="text-xs text-[color:var(--color-text-dim)]">
                {dateRange(e.startDate, e.endDate)}
              </div>
            </div>
            <div className="text-xs text-[color:var(--color-text-muted)]">
              {[e.studyType, e.area].filter(Boolean).join(" · ")}
              {e.score ? ` · ${e.score}` : ""}
            </div>
            {e.courses && e.courses.length > 0 && (
              <div className="mt-1 text-xs text-[color:var(--color-text-dim)]">
                Coursework: {e.courses.join(", ")}
              </div>
            )}
          </div>
        ))}
      </Section>

      <Section title="Skills">
        {(json.skills ?? []).map((s, i) => (
          <div key={i} className="mt-2 first:mt-0">
            <div className="text-xs font-medium uppercase tracking-wide text-[color:var(--color-text-dim)]">
              {s.name}
            </div>
            <div className="mt-0.5 text-sm">{s.keywords.join(" · ")}</div>
          </div>
        ))}
      </Section>

      <Section title="Volunteer">
        {(json.volunteer ?? []).map((v, i) => (
          <Entry
            key={i}
            primary={v.position ?? ""}
            secondary={v.organization ?? ""}
            dates={dateRange(v.startDate, v.endDate)}
            location={null}
            summary={v.summary ?? null}
            highlights={v.highlights ?? []}
            section="volunteer"
            provIndex={provIndex}
            originalBulletText={originalBulletText}
          />
        ))}
      </Section>

      <Section title="Certifications">
        {(json.certificates ?? []).map((c, i) => (
          <div key={i} className="mt-2 flex items-baseline justify-between first:mt-0">
            <div className="text-sm">
              {c.name}
              {c.issuer ? (
                <span className="text-[color:var(--color-text-muted)]"> · {c.issuer}</span>
              ) : null}
            </div>
            <div className="text-xs text-[color:var(--color-text-dim)]">{c.date}</div>
          </div>
        ))}
      </Section>

      <Section title="Publications">
        {(json.publications ?? []).map((p, i) => (
          <div key={i} className="mt-2 first:mt-0">
            <div className="text-sm font-medium">{p.name}</div>
            <div className="text-xs text-[color:var(--color-text-muted)]">
              {[p.publisher, p.releaseDate].filter(Boolean).join(" · ")}
            </div>
            {p.summary && (
              <div className="mt-1 text-xs text-[color:var(--color-text-muted)]">{p.summary}</div>
            )}
          </div>
        ))}
      </Section>

      <Section title="Awards">
        {(json.awards ?? []).map((a, i) => (
          <div key={i} className="mt-2 first:mt-0">
            <div className="text-sm font-medium">{a.title}</div>
            <div className="text-xs text-[color:var(--color-text-muted)]">
              {[a.awarder, a.date].filter(Boolean).join(" · ")}
            </div>
            {a.summary && (
              <div className="mt-1 text-xs text-[color:var(--color-text-muted)]">{a.summary}</div>
            )}
          </div>
        ))}
      </Section>
    </article>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const arr = Array.isArray(children) ? children : [children];
  const filled = arr.filter(Boolean).filter((c) => {
    if (typeof c !== "object") return true;
    return true;
  });
  if (filled.length === 0) return null;
  return (
    <section className="mt-6 first:mt-4">
      <h3 className="text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
        {title}
      </h3>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function Entry({
  primary,
  secondary,
  dates,
  location,
  summary,
  highlights,
  section,
  provIndex,
  originalBulletText,
}: {
  primary: string;
  secondary: string | null;
  dates: string;
  location: string | null;
  summary: string | null;
  highlights: string[];
  section: "work" | "projects" | "volunteer";
  provIndex: Record<string, ProvenanceEntry>;
  originalBulletText: Record<string, string>;
}) {
  return (
    <div className="mt-4 first:mt-0">
      <div className="flex items-baseline justify-between">
        <div className="text-sm font-medium">{primary}</div>
        <div className="text-xs text-[color:var(--color-text-dim)]">{dates}</div>
      </div>
      <div className="flex items-baseline justify-between text-xs text-[color:var(--color-text-muted)]">
        <span>{secondary}</span>
        {location && <span>{location}</span>}
      </div>
      {summary && (
        <p className="mt-1 text-xs text-[color:var(--color-text-muted)]">{summary}</p>
      )}
      {highlights.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {highlights.map((h, i) => {
            const prov = provIndex[`${section}::${h}`];
            const original = prov ? originalBulletText[prov.fact_bullet_id] : undefined;
            const edited = original !== undefined && original !== h;
            return (
              <li key={i} className="text-sm leading-relaxed">
                <div className="flex gap-2">
                  <span className="mt-1.5 size-1 shrink-0 rounded-full bg-[color:var(--color-text-muted)]" />
                  <div>
                    <div>{h}</div>
                    {edited && original && (
                      <div className="mt-0.5 text-xs text-[color:var(--color-text-dim)] line-through opacity-60">
                        {original}
                      </div>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function dateRange(start?: string | null, end?: string | null): string {
  const s = formatMonth(start);
  const e = end ? formatMonth(end) : "Present";
  if (!s && !e) return "";
  return `${s ?? ""}${s ? " – " : ""}${e}`;
}

function formatMonth(iso?: string | null): string | null {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString("en-US", { month: "short", year: "numeric" });
  } catch {
    return iso;
  }
}
