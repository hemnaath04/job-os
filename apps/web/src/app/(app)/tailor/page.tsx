"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  Building2,
  CheckCircle2,
  Download,
  Loader2,
  Plus,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import {
  appwriteWorkspace,
  type AgentJobProgress,
} from "@/lib/appwrite/workspace";
import { isAppwriteWorkspaceEnabled } from "@/lib/appwrite/config";
import { downloadPdf } from "@/lib/download";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { Select } from "@/components/ui/select";
import type {
  GapQuestion,
  JsonResume,
  ProfileFact,
  ProvenanceEntry,
  Resume,
  TailorResponse,
} from "@/lib/types";

// A tailor run lives as an Appwrite agent job on the server. We persist a small
// pointer to the in-flight job in localStorage so navigating away and back (or a
// reload) re-attaches to it instead of losing the run. Cleared on finish/fail.
const ACTIVE_TAILOR_KEY = "tailor:active";
// After this long we give up re-attaching to a stored job and let the user retry.
const TAILOR_MAX_AGE_MS = 20 * 60 * 1_000;
const TAILOR_POLL_MS = 1_500;

type ActiveTailor = {
  jobId: string; // Appwrite agent job id
  resumeId: string; // target resume template
  jobPostingId: string; // JD job posting id (Postgres)
  startedAt: string; // ISO timestamp
};

function loadActiveTailor(): ActiveTailor | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(ACTIVE_TAILOR_KEY);
    return raw ? (JSON.parse(raw) as ActiveTailor) : null;
  } catch {
    return null;
  }
}

function saveActiveTailor(active: ActiveTailor) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(ACTIVE_TAILOR_KEY, JSON.stringify(active));
  } catch {
    /* quota or private mode: the run still works, it just will not survive nav */
  }
}

function clearActiveTailor() {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(ACTIVE_TAILOR_KEY);
  } catch {
    /* non-critical */
  }
}

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
  // The in-flight agent job we are attached to (null when idle). Set both when
  // the user starts a run and when we re-attach to a stored run on mount.
  const [active, setActive] = useState<ActiveTailor | null>(null);
  const [progress, setProgress] = useState<AgentJobProgress | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: resumes = [], isLoading: resumesLoading } = useQuery({
    queryKey: ["resumes"],
    queryFn: () => api.listResumes(),
  });
  const { data: jobs = [], isLoading: jobsLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.listJobs(),
  });

  // Re-attach to an in-flight run left behind by a previous mount (navigation
  // away and back, or a reload). Seed the pickers from it so the finished
  // ResultView and the progress copy have the right resume/job on hand.
  useEffect(() => {
    const stored = loadActiveTailor();
    if (!stored) return;
    if (Date.now() - Date.parse(stored.startedAt) > TAILOR_MAX_AGE_MS) {
      clearActiveTailor();
      return;
    }
    setActive(stored);
    setResumeId(stored.resumeId);
    setJobId(stored.jobPostingId);
  }, []);

  // Auto-pick first non-master resume when none chosen. Lives in an effect
  // (not render-phase setState) so React doesn't schedule extra renders that
  // would clobber focus / click handling on the surrounding form controls.
  const candidateResumes = useMemo(() => resumes.filter((r) => !r.is_master), [resumes]);
  useEffect(() => {
    if (!resumeId && candidateResumes.length > 0) {
      setResumeId(candidateResumes[0].id);
    }
  }, [resumeId, candidateResumes]);

  // Poll the attached agent job until it finishes, surfacing live progress. The
  // agent keeps running server-side regardless, so this only reads state.
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: number | undefined;
    let failures = 0;

    const finish = () => {
      clearActiveTailor();
      setActive(null);
      setProgress(null);
    };

    const poll = async () => {
      if (Date.now() - Date.parse(active.startedAt) > TAILOR_MAX_AGE_MS) {
        finish();
        setError("The previous tailoring run timed out. Try again.");
        return;
      }
      try {
        const current = await appwriteWorkspace.getAgentJob<TailorResponse>(
          active.jobId,
        );
        if (cancelled) return;
        failures = 0;
        if (current.progress) setProgress(current.progress);
        if (current.status === "succeeded") {
          if (current.output) {
            appwriteWorkspace.registerVersionFile(current.output);
            setResult(current.output);
            toast.success("Tailored resume ready");
          }
          finish();
          return;
        }
        if (current.status === "failed") {
          setError(current.error || "The tailoring agent failed.");
          toast.error(current.error || "The tailoring agent failed.");
          finish();
          return;
        }
        timer = window.setTimeout(poll, TAILOR_POLL_MS);
      } catch {
        if (cancelled) return;
        failures += 1;
        if (failures > 8) {
          finish();
          setError("Lost contact with the tailoring run. Refresh to retry.");
          return;
        }
        timer = window.setTimeout(poll, TAILOR_POLL_MS);
      }
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [active]);

  const start = useMutation({
    mutationFn: async () => {
      setError(null);
      // Legacy FastAPI path: no pollable agent job, so keep the old wrapper
      // behavior (wait in memory) and just return the finished version.
      if (!isAppwriteWorkspaceEnabled) {
        const version = await api.tailorResume(resumeId, jobId);
        return { kind: "done" as const, version };
      }
      // Job postings still live in Postgres, so fetch the JD here and hand it to
      // the Appwrite tailor agent, which has the resume + facts but not the job.
      const jobPosting = await api.getJob(jobId);
      const agentJob = await appwriteWorkspace.tailorResume(
        resumeId,
        jobId,
        (jobPosting.jd_parsed ?? {}) as Record<string, unknown>,
        "",
      );
      const record: ActiveTailor = {
        jobId: agentJob.id,
        resumeId,
        jobPostingId: jobId,
        startedAt: new Date().toISOString(),
      };
      saveActiveTailor(record);
      return { kind: "polling" as const, active: record };
    },
    onSuccess: (res) => {
      if (res.kind === "done") {
        setResult(res.version);
        toast.success("Tailored resume ready");
        return;
      }
      setProgress({
        stage: "Starting",
        pct: 0.02,
        updated_at: new Date().toISOString(),
      });
      setActive(res.active);
    },
    onError: (err: Error) => {
      setError(err.message);
      toast.error(err.message);
    },
  });

  const job = jobs.find((j) => j.id === jobId);
  const targetResume = resumes.find((r) => r.id === resumeId);
  const masterResume = resumes.find((r) => r.is_master);
  const hasMaster = !!masterResume;
  const running = !!active || start.isPending;
  const canRun = !!jobId && !!resumeId && hasMaster && !running;

  if (result && targetResume) {
    return (
      <ResultView
        result={result}
        resume={targetResume}
        jobTitle={job?.title ?? "Not selected"}
        companyName={job?.company?.name ?? null}
        onReset={() => {
          setResult(null);
          setError(null);
        }}
      />
    );
  }

  if (active) {
    return (
      <TailorProgress
        stage={progress?.stage ?? "Starting"}
        pct={progress?.pct ?? 0.02}
        jobTitle={job?.title ?? "the selected role"}
        resumeName={targetResume?.name ?? null}
      />
    );
  }

  return (
    <div className="workspace-page max-w-6xl">
      <PageIntro
        eyebrow="Evidence-guided writing studio"
        title="Tailor a resume"
        description="Match a verified career story to a specific role. The agent can reshape and prioritize evidence, but it cannot invent what is not in your profile."
        icon={Sparkles}
      >
        <InfoChip tone="sage">No hallucinated claims</InfoChip>
        <InfoChip>{jobs.length} saved roles</InfoChip>
        <InfoChip tone="clay">{candidateResumes.length} templates ready</InfoChip>
      </PageIntro>

      {!hasMaster && !resumesLoading && (
        <div className="workspace-panel mt-5 flex items-start gap-3 border-amber-400/25 p-5">
          <AlertCircle className="mt-0.5 size-4 text-amber-400" />
          <div className="text-sm">
            <div className="font-medium">No master resume yet</div>
            <p className="text-[color:var(--color-text-muted)]">
              Upload your master PDF on the{" "}
              <Link href="/profile" className="text-[color:var(--color-violet)] underline">
                Profile
              </Link>{" "}
              page first. Tailoring always starts from a clean master baseline.
            </p>
          </div>
        </div>
      )}

      <div className="mt-6 grid gap-5 lg:grid-cols-[minmax(0,1fr)_19rem]">
        <section className="workspace-panel space-y-6 p-6 sm:p-7">
        <Field
          label="Job"
          help="The JD to tailor against. Add jobs from Applications."
        >
          <Select
            value={jobId}
            onChange={setJobId}
            disabled={jobsLoading}
            aria-label="Job to tailor against"
            options={[
              { value: "", label: "Pick a job" },
              ...jobs.map((j) => ({
                value: j.id,
                label: `${j.title}${j.company?.name ? ` · ${j.company.name}` : ""}`,
              })),
            ]}
          />
        </Field>

        <Field
          label="Target resume template"
          help="Where the new tailored version gets saved. Pick a role-specific resume, not Master."
        >
          <TemplatePicker
            value={resumeId}
            onChange={setResumeId}
            candidates={candidateResumes}
            loading={resumesLoading}
          />
        </Field>

        <button
          onClick={() => start.mutate()}
          disabled={!canRun}
          className="kinetic-button kinetic-button-primary disabled:cursor-not-allowed disabled:opacity-40"
        >
          {running ? (
            <>
              <Loader2 className="size-4 animate-spin" /> Tailoring…
            </>
          ) : (
            <>
              <Sparkles className="size-4" /> Tailor resume
            </>
          )}
        </button>

        {running && (
          <p className="text-xs text-[color:var(--color-text-dim)]">
            Drafting, then running a separate quality-model and PDF verification pass.
          </p>
        )}

        {error && !running && (
          <div className="flex items-start gap-2 rounded-xl border border-rose-400/25 bg-rose-400/[0.05] px-4 py-3 text-xs text-rose-200">
            <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        </section>
        <aside className="workspace-panel h-fit overflow-hidden p-6">
          <div className="section-kicker">How the pass works</div>
          <ol className="mt-5 space-y-5">
            {[
              ["01", "Read", "Job requirements and role language"],
              ["02", "Ground", "Your master resume and verified facts"],
              ["03", "Compose", "A traceable draft plus gap questions"],
              ["04", "Verify", "Independent AI review and one-page PDF checks"],
            ].map(([number, title, copy]) => (
              <li key={number} className="flex gap-3">
                <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-[#8A6D12]/15 bg-[#8A6D12]/[.06] font-mono text-[10px] text-[#EAD98A]">{number}</span>
                <div><div className="text-sm font-semibold">{title}</div><p className="mt-0.5 text-xs leading-5 text-[color:var(--color-text-dim)]">{copy}</p></div>
              </li>
            ))}
          </ol>
        </aside>
      </div>
    </div>
  );
}

function TailorProgress({
  stage,
  pct,
  jobTitle,
  resumeName,
}: {
  stage: string;
  pct: number;
  jobTitle: string;
  resumeName: string | null;
}) {
  const percent = Math.round(Math.max(0, Math.min(1, pct)) * 100);
  return (
    <div className="workspace-page max-w-3xl">
      <PageIntro
        eyebrow="Tailoring in progress"
        title="Tailoring your resume"
        description="The agent is grounding a draft in your verified evidence, scoring it against the JD, then running a separate quality and PDF pass."
        icon={Sparkles}
      >
        <InfoChip tone="sage">Safe to leave this page</InfoChip>
      </PageIntro>

      <section className="workspace-panel mt-6 space-y-5 p-6 sm:p-7">
        <div className="flex items-center gap-3">
          <Loader2 className="size-4 shrink-0 animate-spin text-[color:var(--color-violet)]" />
          <div className="text-sm font-medium">{stage}</div>
          <div className="ml-auto text-sm tabular-nums text-[color:var(--color-text-muted)]">
            {percent}%
          </div>
        </div>
        <div
          className="h-2 w-full overflow-hidden rounded-full bg-[color:var(--color-surface-2)]"
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="h-full rounded-full bg-gradient-brand transition-[width] duration-500 ease-out"
            style={{ width: `${percent}%` }}
          />
        </div>
        <p className="text-xs leading-relaxed text-[color:var(--color-text-dim)]">
          Tailoring {resumeName ?? "your resume"} for {jobTitle}. This runs on the
          server, so you can navigate away and come back. The run keeps going and
          this page will show the result when it finishes.
        </p>
      </section>
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
    mutationFn: () => api.finalizeVersion(result.resume_id, result.id),
    onSuccess: () => {
      toast.success("Resume finalized and stored");
      qc.invalidateQueries({ queryKey: ["versions", result.resume_id] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <div className="workspace-page max-w-7xl">
      <button
        onClick={onReset}
        className="inline-flex items-center gap-1.5 text-xs text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]"
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
            className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-xs hover:bg-[color:var(--color-surface-hover)]"
          >
            <RefreshCw className="size-3" /> Re-tailor
          </button>
          <button
            onClick={() =>
              downloadPdf(downloadUrl, `resume_${result.id.slice(0, 8)}.pdf`)
            }
            className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-xs hover:bg-[color:var(--color-surface-hover)]"
          >
            <Download className="size-3" /> Download PDF
          </button>
          <Link
            href={`/resumes/${result.resume_id}/${result.id}`}
            className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-xs hover:bg-[color:var(--color-surface-hover)]"
          >
            <Sparkles className="size-3" /> Edit with AI
          </Link>
          <button
            onClick={() => approve.mutate()}
            disabled={
              result.approved_by_user ||
              approve.isPending ||
              !result.review_report?.passed
            }
            className="inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-4 py-1.5 text-xs font-semibold text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
          >
            <CheckCircle2 className="size-3" />
            {result.approved_by_user ? "Final" : approve.isPending ? "…" : "Finalize"}
          </button>
        </div>
      </header>

      <div
        className={`mt-5 flex flex-wrap items-center gap-3 rounded-xl border px-4 py-3 text-xs ${
          result.review_report?.passed
            ? "border-emerald-400/20 bg-emerald-400/[0.05] text-emerald-200"
            : "border-amber-400/20 bg-amber-400/[0.05] text-amber-100"
        }`}
      >
        {result.review_report?.passed ? (
          <CheckCircle2 className="size-4" />
        ) : (
          <AlertCircle className="size-4" />
        )}
        <span className="font-semibold">
          Quality review{" "}
          {result.review_score !== null ? Math.round(Number(result.review_score)) : "pending"}
          /100
        </span>
        <span className="text-[color:var(--color-text-dim)]">
          {result.review_report?.passed
            ? "Passed. You can finalize now."
            : "Open Edit with AI to resolve the review suggestions before finalizing."}
        </span>
      </div>

      {result.agent_note && (
        <div className="workspace-panel mt-6 border-[#8A6D12]/20 p-5">
          <div className="flex items-start gap-3">
            <Sparkles className="mt-0.5 size-4 text-[color:var(--color-violet)]" />
            <p className="text-sm leading-relaxed text-[color:var(--color-text-muted)]">
              {result.agent_note}
            </p>
          </div>
        </div>
      )}

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-[1.6fr_1fr]">
        <ResumeRender
          json={result.json_resume}
          provenance={result.provenance}
          originalBulletText={originalBulletText}
        />
        <div className="flex flex-col gap-4">
          <AtsPanel
            matched={result.ats_report?.matched ?? []}
            missing={result.ats_report?.missing ?? []}
          />
          {result.gap_questions.length > 0 && (
            <GapPanel gaps={result.gap_questions} facts={facts} />
          )}
        </div>
      </div>
    </div>
  );
}

// ---- Helper components ------------------------------------------------------

function TemplatePicker({
  value,
  onChange,
  candidates,
  loading,
}: {
  value: string;
  onChange: (id: string) => void;
  candidates: Resume[];
  loading: boolean;
}) {
  const qc = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [baseRole, setBaseRole] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.createResume({
        name: name.trim(),
        base_role: baseRole.trim() || null,
        is_master: false,
      }),
    onSuccess: (resume) => {
      toast.success(`Template "${resume.name}" created`);
      qc.invalidateQueries({ queryKey: ["resumes"] });
      onChange(resume.id);
      setCreating(false);
      setName("");
      setBaseRole("");
    },
    onError: (err: Error) => toast.error(err.message),
  });

  // If no candidates, prefer to show the create form prominently.
  const showCreateForm = creating || (candidates.length === 0 && !loading);

  return (
    <div className="space-y-2">
      {candidates.length > 0 && (
        <div className="flex gap-2">
          <Select
            value={value}
            onChange={onChange}
            disabled={loading}
            className="flex-1"
            aria-label="Target resume template"
            options={[
              { value: "", label: "Pick a template" },
              ...candidates.map((r) => ({
                value: r.id,
                label: `${r.name}${r.base_role ? ` · ${r.base_role}` : ""}`,
              })),
            ]}
          />
          <button
            type="button"
            onClick={() => setCreating((c) => !c)}
            className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-xs hover:bg-[color:var(--color-surface-hover)]"
          >
            <Plus className="size-3" /> New
          </button>
        </div>
      )}

      {showCreateForm && (
        <div className="workspace-panel p-4">
          {candidates.length === 0 && (
            <p className="mb-2 text-xs text-[color:var(--color-text-muted)]">
              You only have a Master resume. Create a role-specific template
              here. Tailored versions will save under it.
            </p>
          )}
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Template name (e.g. SWE, ML, AI)"
              className="field-control flex-1"
            />
            <input
              type="text"
              value={baseRole}
              onChange={(e) => setBaseRole(e.target.value)}
              placeholder="Base role (optional)"
              className="field-control sm:w-48"
            />
            <button
              type="button"
              onClick={() => create.mutate()}
              disabled={create.isPending || !name.trim()}
              className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-3 py-2 text-xs font-semibold text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
            >
              {create.isPending ? "Creating…" : "Create"}
            </button>
            {candidates.length > 0 && (
              <button
                type="button"
                onClick={() => setCreating(false)}
                className="rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-2 text-xs text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)]"
              >
                Cancel
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function PageShell({ loading = false }: { loading?: boolean }) {
  return (
    <div className="workspace-page max-w-6xl">
      {loading && <div className="loading-surface" />}
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
  const ringFrom =
    numeric >= 75 ? "#10B981" : numeric >= 50 ? "#F5B544" : "#FF6B8A";
  const ringTo =
    numeric >= 75 ? "#5EEAD4" : numeric >= 50 ? "#F59E0B" : "#F43F5E";
  const pct = Math.max(0, Math.min(100, numeric));
  const circ = 2 * Math.PI * 14;
  const dash = (pct / 100) * circ;
  return (
    <div className="inline-flex items-center gap-2">
      <div className="relative size-9">
        <svg viewBox="0 0 36 36" className="size-9 -rotate-90">
          <defs>
            <linearGradient id={`ats-${pct}`} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor={ringFrom} />
              <stop offset="100%" stopColor={ringTo} />
            </linearGradient>
          </defs>
          <circle
            cx="18"
            cy="18"
            r="14"
            fill="none"
            stroke="rgba(255,255,255,0.08)"
            strokeWidth="3"
          />
          <circle
            cx="18"
            cy="18"
            r="14"
            fill="none"
            stroke={`url(#ats-${pct})`}
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray={`${dash} ${circ}`}
          />
        </svg>
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-[10px] font-semibold">
          {Math.round(pct)}
        </div>
      </div>
      <span className="text-[10px] uppercase tracking-wider text-[color:var(--color-text-dim)]">
        ATS score
      </span>
    </div>
  );
}

function AtsPanel({ matched, missing }: { matched: string[]; missing: string[] }) {
  if (matched.length === 0 && missing.length === 0) return null;
  return (
    <div className="workspace-panel p-5">
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
          <span className="text-xs text-[color:var(--color-text-dim)]">Not available</span>
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
    <div className="workspace-panel border-amber-400/25 p-5">
      <div className="flex items-center gap-2">
        <AlertCircle className="size-4 text-amber-400" />
        <div className="text-sm font-medium">
          Gaps the agent surfaced: {gaps.length} requirement
          {gaps.length === 1 ? "" : "s"} the JD asks for that your profile
          doesn&apos;t cover
        </div>
      </div>
      <ul className="mt-3 space-y-3">
        {gaps.map((g, i) => (
          <GapRow key={i} gap={g} factById={factById} />
        ))}
      </ul>
      <p className="mt-3 text-xs text-[color:var(--color-text-dim)]">
        Adding a fact here marks it verified immediately and re-tailoring will
        include it. Larger entries (a project or experience) are better edited
        on the{" "}
        <Link href="/profile" className="text-[color:var(--color-violet)] underline">
          Profile
        </Link>{" "}
        page.
      </p>
    </div>
  );
}

const GAP_KINDS = [
  "skill",
  "project",
  "experience",
  "certification",
  "publication",
  "award",
] as const;

type GapKind = (typeof GAP_KINDS)[number];

function _suggestKind(requirement: string): GapKind {
  // Very lightweight heuristic: a single-token requirement smells like a skill
  // ("Rust", "GraphQL"); anything multi-word probably needs a richer entry.
  return requirement.trim().split(/\s+/).length === 1 ? "skill" : "project";
}

function GapRow({
  gap,
  factById,
}: {
  gap: GapQuestion;
  factById: Record<string, ProfileFact>;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<GapKind>(_suggestKind(gap.requirement));
  const [title, setTitle] = useState(gap.requirement);
  const [org, setOrg] = useState("");
  const [bullet, setBullet] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.createFact({
        kind,
        title: title.trim(),
        org: org.trim() || null,
        verified: true,
        bullets: bullet.trim()
          ? [{ text: bullet.trim(), metric_verified: true }]
          : [],
      }),
    onSuccess: () => {
      toast.success("Added. Tailor again to include it.");
      qc.invalidateQueries({ queryKey: ["facts"] });
      setOpen(false);
      setOrg("");
      setBullet("");
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const supportsBullets = kind === "project" || kind === "experience";

  return (
    <li className="rounded-[var(--radius-card)] bg-[color:var(--color-surface-2)] p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium">{gap.requirement}</div>
          <div className="mt-1 text-xs text-[color:var(--color-text-muted)]">
            {gap.why_no_match}
          </div>
          {gap.suggested_fact_ids.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {gap.suggested_fact_ids.map((fid) => {
                const f = factById[fid];
                if (!f) return null;
                return (
                  <span
                    key={fid}
                    className="rounded-full bg-[color:var(--color-surface-2)] px-2 py-0.5 text-[11px] text-[color:var(--color-text-muted)]"
                  >
                    nearest: {f.title}
                  </span>
                );
              })}
            </div>
          )}
        </div>
        {!open && (
          <button
            onClick={() => setOpen(true)}
            className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1 text-[11px] hover:bg-[color:var(--color-surface-hover)]"
          >
            <Plus className="size-3" /> Add fact
          </button>
        )}
      </div>

      {open && (
        <div className="mt-3 space-y-2 rounded-[var(--radius-card)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-3">
          <div className="flex flex-wrap gap-1.5">
            {GAP_KINDS.map((k) => (
              <button
                key={k}
                onClick={() => setKind(k)}
                className={`rounded-full border px-2 py-0.5 text-[11px] ${
                  kind === k
                    ? "border-[color:var(--color-purple)]/50 bg-gradient-brand text-[color:var(--color-on-accent)] opacity-90"
                    : "border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-muted)]"
                }`}
              >
                {k}
              </button>
            ))}
          </div>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Title (e.g. 'GraphQL' or 'Real-time inference pipeline')"
            className="glass w-full rounded-[var(--radius-input,10px)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2.5 py-1.5 text-xs outline-none focus:border-[#8A6D12]/60"
          />
          {kind !== "skill" && (
            <input
              value={org}
              onChange={(e) => setOrg(e.target.value)}
              placeholder={
                kind === "experience" ? "Company name" :
                kind === "certification" ? "Issuer" :
                kind === "publication" ? "Publisher" :
                kind === "award" ? "Awarder" :
                "Organization (optional)"
              }
              className="glass w-full rounded-[var(--radius-input,10px)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2.5 py-1.5 text-xs outline-none focus:border-[#8A6D12]/60"
            />
          )}
          {supportsBullets && (
            <textarea
              value={bullet}
              onChange={(e) => setBullet(e.target.value)}
              placeholder="One verified bullet (optional). Keep metrics real."
              rows={2}
              className="glass w-full rounded-[var(--radius-input,10px)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2.5 py-1.5 text-xs outline-none focus:border-[#8A6D12]/60"
            />
          )}
          <div className="flex items-center gap-2">
            <button
              onClick={() => create.mutate()}
              disabled={create.isPending || !title.trim()}
              className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-3 py-1 text-[11px] font-semibold text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.05] disabled:opacity-50"
            >
              {create.isPending ? "Saving…" : "Save fact"}
            </button>
            <button
              onClick={() => setOpen(false)}
              className="rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1 text-[11px] text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)]"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </li>
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
    <article className="workspace-panel p-8">
      {json.basics && (
        <header className="border-b border-[color:var(--color-border)] pb-4">
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
  return `${s ?? ""}${s ? " to " : ""}${e}`;
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
