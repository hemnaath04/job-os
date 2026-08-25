"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  Building2,
  Check,
  CheckCircle2,
  Download,
  Eye,
  LibraryBig,
  Loader2,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useId, useMemo, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { PdfPreviewPane } from "@/components/pdf-preview-pane";
import { reportFailure } from "@/lib/errors";
import {
  appwriteWorkspace,
  type AgentJobProgress,
} from "@/lib/appwrite/workspace";
import { isAppwriteWorkspaceEnabled } from "@/lib/appwrite/config";
import { withTimeout } from "@/lib/async";
import { buildResumeFilename, downloadPdf } from "@/lib/download";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { Field } from "@/components/ui/field";
import { TemplatePicker } from "@/components/template-picker";
import { Select } from "@/components/ui/select";
import type {
  GapQuestion,
  Job,
  JsonResume,
  ProfileFact,
  ProvenanceEntry,
  Resume,
  ResumeReviewResult,
  ResumeVersion,
  TailorResponse,
} from "@/lib/types";

// A tailor run lives as an Appwrite agent job on the server. We persist a small
// pointer to the in-flight job in localStorage so navigating away and back (or a
// reload) re-attaches to it instead of losing the run. Cleared on finish/fail.
const ACTIVE_TAILOR_KEY = "tailor:active";
// The finished run, so leaving for Applications and coming back does not lose
// the result. Only a pointer: the version itself is already saved in Appwrite
// under its resume, and is re-fetched from there on return.
const LAST_TAILOR_KEY = "tailor:last";
// Long enough to survive a detour through the rest of the app, short enough
// that a week-old run does not ambush the next visit.
const LAST_TAILOR_MAX_AGE_MS = 12 * 60 * 60 * 1_000;
// After this long we give up re-attaching to a stored job and let the user retry.
const TAILOR_MAX_AGE_MS = 20 * 60 * 1_000;
// The function flips the job to "running" as its first act, so a job still
// sitting at "queued" past this point never reached the runtime at all (bad
// dispatch, build failure, cold-start crash). Generous enough to cover a cold
// python runtime boot, short enough that the user is not stuck watching a
// spinner for a run that is never coming.
const TAILOR_QUEUED_GRACE_MS = 2 * 60 * 1_000;
const TAILOR_POLL_MS = 1_500;
// Fetching the JD and queueing the execution should be quick. Cap it so a cold
// or unreachable jobs backend surfaces an error instead of spinning forever.
const TAILOR_DISPATCH_TIMEOUT_MS = 45 * 1_000;

type ActiveTailor = {
  jobId: string; // Appwrite agent job id
  resumeId: string; // source resume the output is saved under
  jobPostingId: string; // JD job posting id (Postgres)
  templateId?: string; // the look to render with, absent means the default
  startedAt: string; // ISO timestamp
};

/**
 * What a tailored resume is called: the job it targets, not a company bucket the
 * user had to pick. Hyphen separated, so the name reads the same in the library,
 * in a filename, and in a PDF header.
 */
function jobResumeName(job: Job): string {
  const company = job.company?.name?.trim();
  const title = job.title?.trim();
  return [company, title].filter(Boolean).join(" - ") || "Tailored resume";
}

/**
 * The resume a run should save under, when one already exists for this job.
 *
 * Prefers the job posting id, which is what Tailor writes onto every resume it
 * creates. Falls back to the name for resumes made before that (or on the legacy
 * Postgres path, which has no job column), but only when they carry no job id of
 * their own, so two postings that share a company and title never collide.
 */
function findJobResume(job: Job, resumes: Resume[]): Resume | undefined {
  const byJob = resumes.find((r) => !r.is_master && r.job_posting_id === job.id);
  if (byJob) return byJob;
  const name = jobResumeName(job).toLowerCase();
  return resumes.find(
    (r) =>
      !r.is_master && !r.job_posting_id && r.name.trim().toLowerCase() === name,
  );
}

/** Suffix the name until it stops clashing with a resume that is not this job's. */
function uniqueResumeName(desired: string, resumes: Resume[]): string {
  const taken = new Set(resumes.map((r) => r.name.trim().toLowerCase()));
  if (!taken.has(desired.toLowerCase())) return desired;
  let suffix = 2;
  while (taken.has(`${desired} (${suffix})`.toLowerCase())) suffix += 1;
  return `${desired} (${suffix})`;
}

/** Age of a stored run in ms, or null when the timestamp is unusable. */
function tailorAgeMs(active: ActiveTailor): number | null {
  const startedAt = Date.parse(active.startedAt);
  return Number.isFinite(startedAt) ? Date.now() - startedAt : null;
}

function loadActiveTailor(): ActiveTailor | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(ACTIVE_TAILOR_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ActiveTailor>;
    // A record missing its job id or timestamp can never be polled or aged
    // out, so treat it as absent rather than re-attaching to nothing.
    if (!parsed?.jobId || !parsed.startedAt) return null;
    return parsed as ActiveTailor;
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

type LastTailor = {
  resumeId: string;
  versionId: string;
  jobPostingId: string;
  savedAt: string;
};

function loadLastTailor(): LastTailor | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(LAST_TAILOR_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<LastTailor>;
    if (!parsed?.versionId || !parsed.resumeId || !parsed.savedAt) return null;
    const age = Date.now() - Date.parse(parsed.savedAt);
    if (!Number.isFinite(age) || age > LAST_TAILOR_MAX_AGE_MS) {
      localStorage.removeItem(LAST_TAILOR_KEY);
      return null;
    }
    return parsed as LastTailor;
  } catch {
    return null;
  }
}

function saveLastTailor(last: LastTailor) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(LAST_TAILOR_KEY, JSON.stringify(last));
  } catch {
    /* quota or private mode: the version is still saved server-side */
  }
}

function clearLastTailor() {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(LAST_TAILOR_KEY);
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
  // Not stateful like jobId: no picker ever changes this, it only ever
  // arrives from application-documents.tsx's "Tailor a resume for this role"
  // link and rides straight through to the dispatch call below.
  const applicationId = params.get("application_id") || undefined;

  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string>(initialJobId);
  // Not a choice the user makes. The run resolves or creates a resume named after
  // the job, and this holds it so the progress and result views can label it.
  const [resumeId, setResumeId] = useState<string>("");
  const [result, setResult] = useState<TailorResponse | null>(null);
  // The in-flight agent job we are attached to (null when idle). Set both when
  // the user starts a run and when we re-attach to a stored run on mount.
  const [active, setActive] = useState<ActiveTailor | null>(null);
  const [progress, setProgress] = useState<AgentJobProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  // The look to render with, separate from the source resume that receives the
  // output. Empty means the bundled default.
  const [templateId, setTemplateId] = useState<string>("");
  const { data: availableTemplates = [] } = useQuery({
    queryKey: ["templates"],
    queryFn: () => api.listTemplates(),
  });

  /**
   * Finish the quality pass on the FastAPI container.
   *
   * The Appwrite agent function cannot render PDFs (its runtime has no LaTeX
   * engine), so a fresh tailored version arrives with no PDF and a placeholder
   * review, which leaves Finalize and Download dead. The container ships
   * Tectonic, so hand it the document, then persist the review and PDF onto the
   * Appwrite version. Best effort and non-blocking: the tailored resume is
   * already readable without it, so a failure here only costs the PDF.
   */
  const runReview = useCallback(
    async (version: TailorResponse, look?: string) => {
      if (!isAppwriteWorkspaceEnabled) return;
      const stored = version as TailorResponse & { pdf_file_id?: string | null };
      if (stored.pdf_file_id) return;
      setReviewing(true);
      try {
        const rendered = await api.renderReviewDraft(version.json_resume, {
          templateId: look ?? null,
          // Fires once the PDF and the deterministic (rule-only) score are
          // ready -- before the GitHub-evidence lookup and the model call,
          // which are what make this take a minute plus. Attaching it right
          // away unblocks Download on a real, final-scored PDF instead of
          // making the user wait on the model's advisory notes just to get a
          // file. `reviewing` stays true: the AI review genuinely has not
          // run yet, so QualityStatus's "still checking" state is still
          // correct, it is Download specifically that no longer needs to
          // wait on it (see its disabled condition below).
          onPartial: async (partial) => {
            const reviewedPartial = await appwriteWorkspace.attachReview(version.id, partial);
            setResult((current) =>
              current && current.id === version.id
                ? ({ ...current, ...reviewedPartial } as TailorResponse)
                : current,
            );
          },
        });
        const reviewed = await appwriteWorkspace.attachReview(version.id, rendered);
        setResult((current) =>
          current && current.id === version.id
            ? ({ ...current, ...reviewed } as TailorResponse)
            : current,
        );
        toast.success(
          rendered.review.passed
            ? "Quality review passed. You can finalize now."
            : "Quality review done. See the issues before finalizing.",
        );
      } catch (err) {
        reportFailure("run the quality review", err);
      } finally {
        setReviewing(false);
      }
    },
    [],
  );

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
    const age = tailorAgeMs(stored);
    if (age === null || age > TAILOR_MAX_AGE_MS) {
      clearActiveTailor();
      return;
    }
    setActive(stored);
    setResumeId(stored.resumeId);
    setJobId(stored.jobPostingId);
  }, []);

  // Nothing in flight, but a recent run finished: re-fetch that version from
  // Appwrite and show it again. Without this, visiting Applications and coming
  // back left the user with no way to reach the resume they just generated.
  useEffect(() => {
    if (!isAppwriteWorkspaceEnabled) return;
    const last = loadLastTailor();
    if (!last || loadActiveTailor()) return;
    let cancelled = false;
    void (async () => {
      try {
        const version = await appwriteWorkspace.getVersion(last.versionId);
        if (cancelled) return;
        appwriteWorkspace.registerVersionFile(version);
        setResult(version as TailorResponse);
        setResumeId(last.resumeId);
        setJobId(last.jobPostingId);
      } catch {
        // Archived or otherwise gone. Drop the pointer rather than nagging.
        clearLastTailor();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Every non-master resume is a source resume. Master is the data baseline every
  // run starts from, and templates are their own look-only rows, so neither is
  // ever a save target. Counted for the header chip only.
  const candidateResumes = useMemo(
    () => resumes.filter((r) => !r.is_master),
    [resumes],
  );

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

    const abandon = (message: string) => {
      finish();
      setError(message);
      toast.error(message);
    };

    const poll = async () => {
      const age = tailorAgeMs(active);
      if (age === null || age > TAILOR_MAX_AGE_MS) {
        abandon("The previous tailoring run timed out. Try again.");
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
            saveLastTailor({
              resumeId: current.output.resume_id,
              versionId: current.output.id,
              jobPostingId: active.jobPostingId,
              savedAt: new Date().toISOString(),
            });
            toast.success("Tailored resume ready");
            // Show the resume immediately, then finish the quality pass and
            // the PDF in the background on the container.
            void runReview(current.output, active.templateId);
          } else {
            setError(
              "The tailoring run finished without returning a resume. Try again.",
            );
          }
          finish();
          return;
        }
        if (current.status === "failed") {
          abandon(current.error || "The tailoring agent failed.");
          return;
        }
        // Still "queued" well past a cold start means the agent never picked
        // the job up, so stop waiting and let the user start a fresh run
        // instead of leaving them on a spinner that can never resolve.
        if (current.status === "queued" && age > TAILOR_QUEUED_GRACE_MS) {
          abandon(
            "The tailoring agent never started this run. Try again, and check the agent function if it keeps happening.",
          );
          return;
        }
        timer = window.setTimeout(poll, TAILOR_POLL_MS);
      } catch (err) {
        if (cancelled) return;
        // A missing job row is not a transient network blip, it is a run that
        // cannot be recovered, so do not burn retries waiting on it.
        const message = err instanceof Error ? err.message : "";
        if (/404|could not be found|not found/i.test(message)) {
          abandon("That tailoring run no longer exists. Start a new one.");
          return;
        }
        failures += 1;
        if (failures > 8) {
          abandon("Lost contact with the tailoring run. Try again.");
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
  }, [active, runReview]);

  /**
   * The resume this run saves under, created on the spot if the job has none.
   *
   * A tailored resume belongs to the job it was written for, so the target is
   * derived from the picked job rather than chosen. Re-tailoring the same job
   * adds a version to the resume already named after it. Resolved before dispatch
   * because the agent needs a real, owned resume id to write to.
   */
  const resolveTargetResume = useCallback(
    async (posting: Job): Promise<Resume> => {
      const existing = findJobResume(posting, resumes);
      if (existing) return existing;
      const created = await api.createResume({
        name: uniqueResumeName(jobResumeName(posting), resumes),
        base_role: posting.title?.trim() || null,
        is_master: false,
        job_posting_id: posting.id,
      });
      qc.invalidateQueries({ queryKey: ["resumes"] });
      return created;
    },
    [resumes, qc],
  );

  const start = useMutation({
    mutationFn: async () => {
      setError(null);
      if (!jobId) {
        throw new Error("Pick a job to tailor against first.");
      }
      // Legacy FastAPI path: no pollable agent job, so keep the old wrapper
      // behavior (wait in memory) and just return the finished version. The job
      // is fetched rather than read off the picker list, which holds only the 50
      // newest active postings, so a link from an older application still names
      // its resume correctly instead of failing to find the job.
      if (!isAppwriteWorkspaceEnabled) {
        const target = await resolveTargetResume(await api.getJob(jobId));
        setResumeId(target.id);
        const version = await api.tailorResume(target.id, jobId, applicationId);
        return { kind: "done" as const, version };
      }
      // Job postings still live in Postgres, so fetch the JD here and hand it to
      // the Appwrite tailor agent, which has the resume + facts but not the job.
      // Both steps are raced against a timeout: a cold or unreachable jobs
      // backend used to leave the button spinning with nothing queued and no
      // error to explain it.
      const jobPosting = await withTimeout(
        api.getJob(jobId),
        TAILOR_DISPATCH_TIMEOUT_MS,
        "Could not load the job description. The jobs backend may be waking up, try again in a moment.",
      );
      const jdParsed = (jobPosting.jd_parsed ?? {}) as Record<string, unknown>;
      if (Object.keys(jdParsed).length === 0) {
        throw new Error(
          "This job has no parsed description yet, so there is nothing to tailor against. Re-import the job from its URL first.",
        );
      }
      // Resolved after the JD check so a job with nothing to tailor against does
      // not leave an empty resume behind, and before dispatch because the agent
      // writes its version under this id.
      const target = await resolveTargetResume(jobPosting);
      setResumeId(target.id);
      const agentJob = await withTimeout(
        appwriteWorkspace.tailorResume(target.id, jobId, jdParsed, "", applicationId),
        TAILOR_DISPATCH_TIMEOUT_MS,
        "Could not queue the tailoring agent. Check your connection and try again.",
      );
      const record: ActiveTailor = {
        jobId: agentJob.id,
        resumeId: target.id,
        jobPostingId: jobId,
        templateId: templateId || undefined,
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
      reportFailure("start that tailoring run", err);
    },
  });

  const job = jobs.find((j) => j.id === jobId);
  const targetResume = resumes.find((r) => r.id === resumeId);
  const masterResume = resumes.find((r) => r.is_master);
  const hasMaster = !!masterResume;
  const running = !!active || start.isPending;
  const canRun = !!jobId && hasMaster && !running;
  // What the run will save under, shown on the form so the outcome is not a
  // surprise: an existing job resume gets another version, a new job gets a new
  // resume named after it.
  const plannedResume = job ? findJobResume(job, resumes) : undefined;
  const plannedName = job
    ? (plannedResume?.name ?? uniqueResumeName(jobResumeName(job), resumes))
    : null;
  // A run in flight is already saving under a known resume, so prefer that name
  // over the plan for the job currently in the picker.
  const runResumeName = targetResume?.name ?? plannedName;

  // Render a finished run even when the target resume is not in the list yet
  // (still loading, or archived while the agent worked). Gating the result on
  // the resume lookup used to drop a successful run on the floor and drop the
  // user back on an empty form with nothing to show for it.
  if (result) {
    return (
      <ResultView
        result={result}
        resumeName={runResumeName ?? "Tailored resume"}
        jobTitle={job?.title ?? "Not selected"}
        companyName={job?.company?.name ?? null}
        reviewing={reviewing}
        templateId={templateId || null}
        onRunReview={() => void runReview(result, templateId || undefined)}
        onFinalized={(version) =>
          setResult((current) =>
            current ? ({ ...current, ...version } as TailorResponse) : current,
          )
        }
        onReset={() => {
          setResult(null);
          setError(null);
          clearLastTailor();
        }}
        onTailorAgain={() => {
          // Same job, same resume: rerun the exact run this page is already
          // configured for, rather than sending the user back to the picker
          // to choose the very same job again. jobId/resumeId are untouched,
          // so `start` targets this run's own job and adds another version.
          setResult(null);
          setError(null);
          start.mutate();
        }}
        onVersionDeleted={() => {
          setResult(null);
          setError(null);
          clearLastTailor();
        }}
      />
    );
  }

  if (active) {
    return (
      <TailorProgress
        stage={progress?.stage ?? "Starting"}
        step={progress?.step ?? null}
        detail={progress?.detail ?? null}
        pct={progress?.pct ?? 0.02}
        jobTitle={job?.title ?? "the selected role"}
        resumeName={runResumeName}
        startedAt={active.startedAt}
        onCancel={() => {
          // Stop watching and return to the form. The server run cannot be
          // aborted mid-execution, so it may still finish and save a version;
          // this just detaches the UI so the user is not stuck on the spinner.
          clearActiveTailor();
          setActive(null);
          setProgress(null);
          setError(null);
        }}
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
        <InfoChip tone="clay">{candidateResumes.length} source resumes</InfoChip>
      </PageIntro>

      {!hasMaster && !resumesLoading && (
        <div className="notice notice-caution mt-5 flex items-start gap-3 p-5">
          <AlertCircle className="mt-0.5 size-4 shrink-0" />
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
          help="The JD to tailor against, and the name the tailored resume takes. Add jobs from Applications."
        >
          {(control) => (
            <>
              <Select
                {...control}
                // The preview below is the only signal about where the run
                // lands, so describe the picker with it as well as the help.
                aria-describedby={
                  plannedName
                    ? [control["aria-describedby"], "tailor-output-name"]
                        .filter(Boolean)
                        .join(" ")
                    : control["aria-describedby"]
                }
                value={jobId}
                onChange={setJobId}
                disabled={jobsLoading}
                options={[
                  { value: "", label: "Pick a job" },
                  ...jobs.map((j) => ({
                    value: j.id,
                    label: `${j.title}${j.company?.name ? ` · ${j.company.name}` : ""}`,
                  })),
                ]}
              />
              {plannedName && (
                <p
                  id="tailor-output-name"
                  className="mt-2 text-xs text-[color:var(--color-text-muted)]"
                >
                  {plannedResume
                    ? `Adds a version to your existing "${plannedName}" resume.`
                    : `Saves as a new source resume, "${plannedName}".`}
                </p>
              )}
            </>
          )}
        </Field>

        <Field
          label="Template"
          help="The look the PDF is rendered with, and nothing else: the template is read, never written to. Every preview is a real render of invented sample data. Nothing selected uses Jake's Resume, the single-column one that parses most reliably."
        >
          {() => (
            <TemplatePicker
              templates={availableTemplates}
              value={templateId}
              onChange={setTemplateId}
            />
          )}
        </Field>

        <button
          onClick={() => start.mutate()}
          disabled={!canRun}
          className="kinetic-button product-button-gradient disabled:cursor-not-allowed disabled:opacity-40"
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
          <div className="notice notice-critical flex items-start gap-2 px-4 py-3 text-xs">
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
                <span className="product-icon size-8 font-mono text-[10px]">{number}</span>
                <div><div className="text-sm font-semibold">{title}</div><p className="mt-0.5 text-xs leading-5 text-[color:var(--color-text-dim)]">{copy}</p></div>
              </li>
            ))}
          </ol>
        </aside>
      </div>
    </div>
  );
}

/**
 * The steps a tailor run walks through, in the order the agent emits them.
 *
 * `step` matches the stable id the agent writes onto the job row, so the label
 * shown here can be reworded without the checklist losing its place. Optional
 * steps only exist on some runs: a repair pass runs when the first draft leaves
 * something a repair can honestly fix, and is skipped when it does not.
 */
const TAILOR_STEPS: { step: string; label: string; optional?: boolean }[] = [
  { step: "load_profile", label: "Opening your profile" },
  { step: "read_role", label: "Reading the role" },
  { step: "match_evidence", label: "Matching your verified evidence" },
  { step: "find_gaps", label: "Finding the real gaps" },
  { step: "compose", label: "Composing your resume" },
  { step: "check_claims", label: "Checking every claim is backed" },
  { step: "repair", label: "Tightening the weak spots", optional: true },
  { step: "check_repair", label: "Rechecking every claim", optional: true },
  { step: "assemble", label: "Assembling the page" },
  { step: "check_draft", label: "Reviewing the draft" },
  { step: "save_draft", label: "Saving your draft" },
  // The agent reports this once the version row exists, a poll or so before this
  // screen is replaced by the result. Without a row for it the checklist would
  // blink out for that last second.
  { step: "done", label: "Finished" },
];

/**
 * Rough relative weight of each REQUIRED step, for the estimated fallback
 * below. Not measured durations, just an ordering of "fast" vs "slow" from
 * the same knowledge already in this file's own comments: composing is a
 * single model call that can run well over a minute, the claim-check that
 * follows it does real work too, and the rest is bookkeeping around them.
 * Optional steps (repair, check_repair) are excluded, same as the real
 * checklist excludes them until a run actually reaches one.
 */
const TAILOR_ESTIMATE_WEIGHTS: Record<string, number> = {
  load_profile: 1,
  read_role: 1,
  match_evidence: 2,
  find_gaps: 2,
  compose: 10,
  check_claims: 4,
  assemble: 1,
  check_draft: 2,
  save_draft: 1,
  done: 1,
};

/**
 * A step index synthesized from elapsed time alone, for the rare run where
 * the agent's own progress writes never arrive (best-effort on that side; see
 * update_job_progress). This exists so "click Tailor" never regresses to a
 * bare spinner with no story, but it must never be confused for the real
 * thing: callers only use this before any real `progress.step` has been
 * seen, and switch to real data permanently the moment one arrives.
 */
function useEstimatedStep(startedAt: string): {
  index: number;
  label: string;
} {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, []);

  const required = TAILOR_STEPS.filter((entry) => !entry.optional);
  const totalWeight = required.reduce(
    (sum, entry) => sum + (TAILOR_ESTIMATE_WEIGHTS[entry.step] ?? 1),
    0,
  );
  // Calibrated so the weighted midpoint (compose, the heaviest step) lands
  // around 45s in, matching this run's own documented "well over a minute"
  // for that one step without claiming false precision for the rest.
  const secondsPerWeightUnit = 4.5;
  const elapsedSeconds = Math.max(0, (now - Date.parse(startedAt)) / 1_000);

  let consumed = 0;
  let index = 0;
  for (let i = 0; i < required.length; i += 1) {
    const weight = TAILOR_ESTIMATE_WEIGHTS[required[i].step] ?? 1;
    const stepSeconds = weight * secondsPerWeightUnit;
    if (elapsedSeconds < consumed + stepSeconds || i === required.length - 1) {
      index = TAILOR_STEPS.findIndex((entry) => entry.step === required[i].step);
      break;
    }
    consumed += stepSeconds;
  }
  return { index, label: TAILOR_STEPS[index]?.label ?? "" };
}

/**
 * How long the current step has been running, in whole seconds.
 *
 * A composing step is a single model call that can take well over a minute, and
 * a line that never changes for that long reads as a hang. This is the honest
 * thing to show meanwhile: not a fake percentage, just how long the real work
 * has been going.
 */
function useStepElapsed(step: string | null): number {
  const [startedAt, setStartedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    setStartedAt(Date.now());
    setNow(Date.now());
  }, [step]);

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, []);

  return Math.max(0, Math.floor((now - startedAt) / 1_000));
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

function TailorProgress({
  stage,
  step,
  detail,
  pct,
  jobTitle,
  resumeName,
  startedAt,
  onCancel,
}: {
  stage: string;
  step: string | null;
  detail: string | null;
  pct: number;
  jobTitle: string;
  resumeName: string | null;
  startedAt: string;
  onCancel: () => void;
}) {
  // The agent's own progress writes are best-effort (see update_job_progress)
  // and can go missing for a whole run. `step` is null the entire time when
  // that happens, which is the one case this falls back to elapsed-time
  // estimation instead of the real thing. The moment a real step name shows
  // up, this stops being consulted at all -- there is no path back to
  // estimating once truth is available.
  const hasRealSignal = step !== null;
  const estimated = useEstimatedStep(startedAt);
  const currentIndex = hasRealSignal
    ? TAILOR_STEPS.findIndex((entry) => entry.step === step)
    : estimated.index;
  const displayStage = hasRealSignal ? stage : estimated.label;
  const requiredCount = TAILOR_STEPS.filter((entry) => !entry.optional).length;
  const percent = hasRealSignal
    ? Math.round(Math.max(0, Math.min(1, pct)) * 100)
    : Math.round(Math.max(2, Math.min(96, (currentIndex / requiredCount) * 100)));
  const elapsed = useStepElapsed(hasRealSignal ? step : estimated.label);
  // An optional step is only real once the run has reached it. Hiding the rest
  // keeps the list a description of this run rather than of every possible run.
  const rows = TAILOR_STEPS.map((entry, index) => ({ ...entry, index })).filter(
    (entry) => !entry.optional || (currentIndex >= 0 && entry.index <= currentIndex),
  );

  return (
    <div className="workspace-page max-w-3xl">
      <PageIntro
        eyebrow="Tailoring in progress"
        title="Tailoring your resume"
        description="The agent reads the role against your verified evidence, writes from what genuinely matches, then checks every claim on the page is backed."
        icon={Sparkles}
      >
        <InfoChip tone="sage">Safe to leave this page</InfoChip>
      </PageIntro>

      <section className="workspace-panel mt-6 space-y-5 p-6 sm:p-7">
        {/* The run takes minutes, so the current step is announced rather than
            only redrawn, and the elapsed time next to it is real. */}
        <div role="status" aria-live="polite" className="flex items-center gap-3">
          <Loader2
            className="size-4 shrink-0 animate-spin text-[color:var(--color-violet)]"
            aria-hidden="true"
          />
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">{displayStage}</div>
            {hasRealSignal && detail ? (
              <div className="truncate text-xs text-[color:var(--color-text-muted)]">
                {detail}
              </div>
            ) : !hasRealSignal ? (
              <div className="truncate text-xs text-[color:var(--color-text-dim)]">
                Estimated from typical run timing, not a live update
              </div>
            ) : null}
          </div>
          <div className="ml-auto text-sm tabular-nums text-[color:var(--color-text-muted)]">
            {percent}%{!hasRealSignal && <span className="ml-1 text-[10px] align-top">est.</span>}
          </div>
        </div>
        <div
          className="h-2 w-full overflow-hidden rounded-full bg-[color:var(--color-surface-2)]"
          role="progressbar"
          aria-label="Tailoring progress"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className="h-full rounded-full bg-gradient-brand transition-[width] duration-500 ease-out"
            style={{ width: `${percent}%` }}
          />
        </div>

        {/* Only drawn once the agent has named a step. A deployment where the
            function still reports stage text alone falls back to the line and
            bar above rather than to a checklist with nothing selected. */}
        {currentIndex >= 0 && (
          <ol className="space-y-2.5">
            {rows.map((entry) => {
              const done = entry.index < currentIndex;
              const current = entry.index === currentIndex;
              return (
                <li
                  key={entry.step}
                  aria-current={current ? "step" : undefined}
                  className="flex items-start gap-2.5 text-sm"
                >
                  <span
                    aria-hidden="true"
                    className={`mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border ${
                      done
                        ? "border-transparent bg-[color:var(--color-mint-ink)] text-white"
                        : current
                          ? "border-[color:var(--color-violet)]"
                          : "border-[color:var(--color-border)]"
                    }`}
                  >
                    {done && <Check className="size-2.5" strokeWidth={3} />}
                    {current && (
                      <span className="size-1.5 animate-pulse rounded-full bg-[color:var(--color-violet)]" />
                    )}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div
                      className={
                        current
                          ? "font-medium text-[color:var(--color-text)]"
                          : done
                            ? "text-[color:var(--color-text-muted)]"
                            : "text-[color:var(--color-text-dim)]"
                      }
                    >
                      {current ? displayStage : entry.label}
                    </div>
                    {current && hasRealSignal && detail && (
                      <p className="mt-0.5 text-xs text-[color:var(--color-text-muted)]">
                        {detail}
                      </p>
                    )}
                  </div>
                  {current && (
                    <span className="shrink-0 text-xs tabular-nums text-[color:var(--color-text-dim)]">
                      {formatElapsed(elapsed)}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        )}

        <p className="text-xs leading-relaxed text-[color:var(--color-text-dim)]">
          Tailoring {resumeName ?? "your resume"} for {jobTitle}. This runs on the
          server, so you can navigate away and come back. The run keeps going and
          this page will show the result when it finishes.
        </p>
        <div className="flex justify-end">
          <button
            type="button"
            onClick={onCancel}
            className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-xs text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
          >
            <X className="size-3" /> Cancel
          </button>
        </div>
      </section>
    </div>
  );
}

/**
 * True when the review never actually ran, as opposed to running and finding
 * real problems. The agent function emits this marker when its render step could
 * not load WeasyPrint's native libs, which is the normal outcome there.
 */
function reviewNeedsRetry(result: TailorResponse): boolean {
  return (result.review_report?.issues ?? []).some(
    (issue) => issue.code === "review_unavailable",
  );
}

function ResultView({
  result,
  // Not shown in the header, which derives its own label from the company and
  // job title -- read only by the delete confirmation below, which is about
  // the resume/version pair, not the job.
  resumeName,
  jobTitle,
  companyName,
  reviewing,
  templateId,
  onRunReview,
  onFinalized,
  onReset,
  onTailorAgain,
  onVersionDeleted,
}: {
  result: TailorResponse;
  resumeName: string;
  jobTitle: string;
  companyName: string | null;
  reviewing: boolean;
  templateId: string | null;
  onRunReview: () => void;
  onFinalized: (version: ResumeVersion) => void;
  onReset: () => void;
  onTailorAgain: () => void;
  onVersionDeleted: () => void;
}) {
  const qc = useQueryClient();
  const downloadUrl = api.downloadVersionUrl(result.resume_id, result.id);

  // Archiving, not a hard delete: the version stays in the database (same
  // guarantee every other "remove" action on a version makes elsewhere in
  // this app), just off this resume's active list and out of this page.
  const deleteVersion = useMutation({
    mutationFn: () => appwriteWorkspace.archiveVersion(result.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["versions", result.resume_id] });
      toast.success("Version archived", {
        description: "It remains stored in the database.",
      });
      onVersionDeleted();
    },
    onError: (err: Error) => reportFailure("archive this version", err),
  });

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

  // Preview first: the real rendered PDF is the actual deliverable, and it's
  // what the whole run was for. Evidence (the per-bullet diff against the
  // verified facts backing each rewrite) is one click away for whoever wants
  // to audit what changed and why, not the first thing shown.
  const [view, setView] = useState<"preview" | "evidence">("preview");
  const previewQuery = useQuery({
    queryKey: ["tailor-result-preview", result.id, templateId],
    // `?? {}` matches the guard resume-editor-client.tsx already has on its own
    // previewDraft call. json_resume is a required field on the backend and
    // JSON.stringify silently drops an undefined one, so a `result` that
    // arrived without it would 422 rather than render. Defensive only: the 422s
    // this page was actually showing were the template id being sent as a
    // bundled template key, fixed in `previewDraft` itself.
    queryFn: () => api.previewDraft(result.json_resume ?? {}, templateId),
    enabled: view === "preview",
  });

  // Issues the review raised, held so the user can read them and decide. The
  // review advises; it does not gate. An honest resume scores in the seventies,
  // so refusing anything short of a pass meant never being able to finalize.
  const [blockedReview, setBlockedReview] = useState<ResumeReviewResult | null>(
    null,
  );
  const [showAllBlockedIssues, setShowAllBlockedIssues] = useState(false);
  const approve = useMutation({
    mutationFn: (force: boolean) =>
      // Same look the review rendered with, so the finalized PDF matches what
      // the user saw rather than silently reverting to the default.
      api.finalizeVersion(result.resume_id, result.id, { force, templateId }),
    onSuccess: (outcome) => {
      qc.invalidateQueries({ queryKey: ["versions", result.resume_id] });
      if (outcome.status === "blocked") {
        setBlockedReview(outcome.review);
        toast.warning("Review found issues. Read them, then finalize anyway.");
        return;
      }
      setBlockedReview(null);
      onFinalized(outcome.version);
      toast.success("Resume finalized and stored");
    },
    onError: (err: Error) => reportFailure("finalize this resume", err),
  });

  return (
    <div className="workspace-page max-w-7xl">
      <div className="flex items-center justify-between gap-4">
        <button
          onClick={onReset}
          className="inline-flex items-center gap-1.5 text-xs text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]"
        >
          <ArrowLeft className="size-3" /> Tailor another
        </button>
        <button
          onClick={() => {
            if (
              window.confirm(
                `Archive this version of ${resumeName}? It will remain stored.`,
              )
            ) {
              deleteVersion.mutate();
            }
          }}
          disabled={deleteVersion.isPending}
          className="inline-flex items-center gap-1.5 text-xs text-[color:var(--color-text-muted)] hover:text-[color:var(--color-rose-ink)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          {deleteVersion.isPending ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <Trash2 className="size-3" />
          )}
          Delete this version
        </button>
      </div>

      <header className="mt-3 flex flex-wrap items-start justify-between gap-4">
        <div>
          {/* The job title alone. `resumeName` is derived as
              "{company} - {title}", so pairing it with the title printed the
              title twice and the company twice over, once here and again in the
              row below. The resume's name is what the library is for. */}
          <h1 className="text-2xl font-medium tracking-tight">{jobTitle}</h1>
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
          <QualityStatus
            reviewing={reviewing}
            result={result}
            onRunReview={onRunReview}
          />
          {/* Distinct from "Tailor another" at the top of the page, which
              goes back to the picker to choose a different job. This reruns
              the exact same job against the same resume in place, adding
              another version rather than replacing this one -- for when the
              draft is usable but a second pass might reach a better score. */}
          <button
            onClick={onTailorAgain}
            disabled={reviewing}
            className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-xs hover:bg-[color:var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <RefreshCw className="size-3" /> Tailor again
          </button>
          <button
            onClick={() =>
              downloadPdf(
                downloadUrl,
                buildResumeFilename([result.json_resume?.basics?.name, companyName, jobTitle]),
              )
            }
            // Not gated on `reviewing`: once a real PDF is attached (which
            // now happens as soon as the deterministic score is ready, not
            // once the full AI review finishes -- see runReview's onPartial),
            // there is a real file to download. `downloadUrl` is the actual
            // gate; waiting on `reviewing` too made Download sit disabled for
            // the whole minute-plus the model review used to take, for a PDF
            // that had already been sitting there, finished and unchanging.
            disabled={!downloadUrl}
            className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-xs hover:bg-[color:var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Download className="size-3" /> Download PDF
          </button>
          <Link
            href={`/resumes/${result.resume_id}/${result.id}`}
            className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-xs hover:bg-[color:var(--color-surface-hover)]"
          >
            <Sparkles className="size-3" /> Edit with AI
          </Link>
          {/* This version is already saved under its resume, so give the user a
              way back to it that does not depend on this page's state. */}
          <Link
            href={`/resumes?open=${result.resume_id}`}
            className="inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-xs hover:bg-[color:var(--color-surface-hover)]"
          >
            <LibraryBig className="size-3" /> In library
          </Link>
          <button
            onClick={() => approve.mutate(false)}
            // Only a finished version or an in-flight request disables this.
            // A failing review does not: it is advice, and the user decides.
            disabled={result.approved_by_user || approve.isPending || reviewing}
            className="inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-4 py-1.5 text-xs font-semibold text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
          >
            {approve.isPending ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <CheckCircle2 className="size-3" />
            )}
            {result.approved_by_user
              ? "Final"
              : approve.isPending
                ? "Finalizing…"
                : "Finalize"}
          </button>
        </div>
      </header>

      {blockedReview && !result.approved_by_user && (
        <div className="notice notice-caution mt-5 p-4 text-xs">
          <div className="flex items-center gap-2">
            <AlertCircle className="size-4 shrink-0" />
            <span className="font-semibold">
              {/* The score itself lives on the Quality Review status next to
                  Finalize, one line up. Restating it here just made the same
                  number the first thing you'd read twice in a row. */}
              The review flagged {blockedReview.issues.length} issue
              {blockedReview.issues.length === 1 ? "" : "s"}
            </span>
          </div>
          <p className="notice-detail mt-1.5">
            This is advice, not a gate. Fix what you agree with in Edit with AI,
            or finalize as is.
          </p>
          <ul className="mt-2.5 space-y-1">
            {(showAllBlockedIssues
              ? blockedReview.issues
              : blockedReview.issues.slice(0, 6)
            ).map((issue, index) => (
              <li key={index} className="flex gap-2">
                <span className="shrink-0 font-mono text-[10px] uppercase opacity-60">
                  {issue.severity}
                </span>
                <span>{issue.message}</span>
              </li>
            ))}
          </ul>
          {!showAllBlockedIssues && blockedReview.issues.length > 6 && (
            <button
              onClick={() => setShowAllBlockedIssues(true)}
              className="notice-detail mt-1 underline decoration-dotted hover:text-[color:var(--color-text)]"
            >
              show {blockedReview.issues.length - 6} more
            </button>
          )}
          <div className="mt-3 flex items-center gap-2">
            <button
              onClick={() => approve.mutate(true)}
              disabled={approve.isPending}
              className="inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-3 py-1.5 text-[11px] font-semibold text-[color:var(--color-on-accent)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
            >
              <CheckCircle2 className="size-3" />
              {approve.isPending ? "Finalizing…" : "Finalize anyway"}
            </button>
            <button
              onClick={() => setBlockedReview(null)}
              className="rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1.5 text-[11px] text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]"
            >
              Not yet
            </button>
          </div>
        </div>
      )}

      {result.agent_note && (
        <div className="workspace-panel mt-6 border-[color:var(--color-accent-border)] p-5">
          <div className="flex items-start gap-3">
            <Sparkles className="mt-0.5 size-4 text-[color:var(--color-violet)]" />
            <p className="text-sm leading-relaxed text-[color:var(--color-text-muted)]">
              {result.agent_note}
            </p>
          </div>
        </div>
      )}

      <div
        role="group"
        aria-label="Result view"
        className="mt-6 flex w-fit rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-0.5"
      >
        <button
          type="button"
          onClick={() => setView("preview")}
          aria-pressed={view === "preview"}
          className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition ${view === "preview" ? "bg-[color:var(--color-surface-hover)] text-[color:var(--color-text)]" : "text-[color:var(--color-text-dim)] hover:text-[color:var(--color-text)]"}`}
        >
          <Eye className="size-3.5" /> Preview
        </button>
        <button
          type="button"
          onClick={() => setView("evidence")}
          aria-pressed={view === "evidence"}
          className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs transition ${view === "evidence" ? "bg-[color:var(--color-surface-hover)] text-[color:var(--color-text)]" : "text-[color:var(--color-text-dim)] hover:text-[color:var(--color-text)]"}`}
        >
          <ShieldCheck className="size-3.5" /> Evidence
        </button>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-[1.6fr_1fr]">
        {view === "preview" ? (
          <div className="product-panel h-[78dvh]">
            <PdfPreviewPane query={previewQuery} />
          </div>
        ) : (
          <ResumeRender
            json={result.json_resume}
            provenance={result.provenance}
            originalBulletText={originalBulletText}
          />
        )}
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

function PageShell({ loading = false }: { loading?: boolean }) {
  return (
    <div className="workspace-page max-w-6xl">
      {loading && <div className="loading-surface" />}
    </div>
  );
}

// Used to be a full-width `notice` banner sitting between the header and the
// resume, the same visual weight as the Keyword Match ring above it and the
// blocked-review notice below it — three things reading as competing grades
// on one scroll. It carries real information (in-progress state, pass/fail,
// a retry action) so nothing here was cut, only shrunk and moved next to the
// button it is actually a pre-flight check for.
function QualityStatus({
  reviewing,
  result,
  onRunReview,
}: {
  reviewing: boolean;
  result: TailorResponse;
  onRunReview: () => void;
}) {
  if (reviewing) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-[color:var(--color-text-muted)]">
        <Loader2 className="size-3.5 animate-spin" /> Reviewing…
      </span>
    );
  }
  const passed = result.review_report?.passed;
  const label =
    result.review_score !== null
      ? `Review ${Math.round(Number(result.review_score))}/100`
      : "Review pending";
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs ${
        passed
          ? "text-[color:var(--color-mint-ink)]"
          : "text-[color:var(--color-text-muted)]"
      }`}
      title={
        passed
          ? "Passed. You can finalize now."
          : reviewNeedsRetry(result)
            ? "The review could not complete. Run it again to enable Finalize and the PDF."
            : "Open Edit with AI to resolve the review suggestions before finalizing."
      }
    >
      {passed ? (
        <CheckCircle2 className="size-3.5" />
      ) : (
        <AlertCircle className="size-3.5" />
      )}
      {label}
      {!passed && (
        <button
          onClick={onRunReview}
          className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-border)] px-2 py-0.5 text-[10px] hover:bg-[color:var(--color-surface-hover)]"
        >
          <RefreshCw className="size-2.5" /> Run
        </button>
      )}
    </span>
  );
}

function AtsBadge({ score }: { score: string | null }) {
  // Null here means the backend explicitly withheld a number (the JD failed
  // to parse, or named nothing this scorer could check) rather than "not
  // computed yet" -- ResultView only renders once a run has finished. Saying
  // so beats silently showing nothing, which reads as the ring simply being
  // gone rather than as a real, honest "we don't know" state.
  if (score === null || score === undefined) {
    return (
      <span className="text-[10px] uppercase tracking-wider text-[color:var(--color-text-dim)]">
        Keyword Match unavailable
      </span>
    );
  }
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
        Keyword Match
      </span>
    </div>
  );
}

function AtsPanel({ matched, missing }: { matched: string[]; missing: string[] }) {
  if (matched.length === 0 && missing.length === 0) return null;
  return (
    <div className="workspace-panel p-5">
      <div className="text-xs font-medium uppercase tracking-wide text-[color:var(--color-text-dim)]">
        Keyword coverage
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
      ? "bg-[color:var(--color-mint)]/10 text-[color:var(--color-mint-ink)]"
      : "bg-[color:var(--color-rose)]/10 text-[color:var(--color-rose-ink)]";
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
            // rounded-full, not rounded-lg: fine for a short keyword, but a
            // few of these are full JD phrases ("Experience using modern AI
            // systems such as...") that wrap to two or three lines, and a
            // pill's radius is half its own height -- on a wrapped block
            // that reads as a bulging stadium shape with the text spilling
            // past the curve at both ends instead of a normal tag.
            className={`rounded-lg px-2 py-0.5 text-[11px] leading-relaxed ${colorClass}`}
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
    <div className="notice notice-caution p-5">
      <div className="flex items-center gap-2">
        <AlertCircle className="size-4 shrink-0" />
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
  // One row per gap, so the ids have to be unique per row.
  const gapFormId = useId();
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
    onError: (err: Error) => reportFailure("add that fact", err),
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
          <div role="group" aria-label="Kind of fact" className="flex flex-wrap gap-1.5">
            {GAP_KINDS.map((k) => (
              <button
                key={k}
                onClick={() => setKind(k)}
                aria-pressed={kind === k}
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
          <label htmlFor={`${gapFormId}-title`} className="sr-only">
            Title
          </label>
          <input
            id={`${gapFormId}-title`}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Title (e.g. 'GraphQL' or 'Real-time inference pipeline')"
            className="glass w-full rounded-[var(--radius-input,10px)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2.5 py-1.5 text-base outline-none sm:text-xs focus:border-[color:var(--color-accent-border)]"
          />
          {kind !== "skill" && (
            <>
            <label htmlFor={`${gapFormId}-org`} className="sr-only">
              Organization
            </label>
            <input
              id={`${gapFormId}-org`}
              value={org}
              onChange={(e) => setOrg(e.target.value)}
              placeholder={
                kind === "experience" ? "Company name" :
                kind === "certification" ? "Issuer" :
                kind === "publication" ? "Publisher" :
                kind === "award" ? "Awarder" :
                "Organization (optional)"
              }
              className="glass w-full rounded-[var(--radius-input,10px)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2.5 py-1.5 text-base outline-none sm:text-xs focus:border-[color:var(--color-accent-border)]"
            />
            </>
          )}
          {supportsBullets && (
            <>
            <label htmlFor={`${gapFormId}-bullet`} className="sr-only">
              Bullet
            </label>
            <textarea
              id={`${gapFormId}-bullet`}
              value={bullet}
              onChange={(e) => setBullet(e.target.value)}
              placeholder="One verified bullet (optional). Keep metrics real."
              rows={2}
              className="glass w-full rounded-[var(--radius-input,10px)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2.5 py-1.5 text-base outline-none sm:text-xs focus:border-[color:var(--color-accent-border)]"
            />
            </>
          )}
          <div className="flex items-center gap-2">
            <button
              onClick={() => create.mutate()}
              disabled={create.isPending || !title.trim()}
              className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-3 py-1 text-[11px] font-semibold text-[color:var(--color-on-accent)] shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
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
              <li key={i} className="group text-sm leading-relaxed">
                <div className="flex gap-2">
                  <span className="mt-1.5 size-1 shrink-0 rounded-full bg-[color:var(--color-text-muted)]" />
                  <div className="min-w-0">
                    <div>
                      {h}
                      {/* A quiet marker that this line was rewritten. The
                          original used to sit under every edited bullet
                          permanently, which doubled the length of the page and
                          made the preview read as duplicated text rather than
                          as a resume. It is still one hover away, which is
                          where a provenance detail belongs. */}
                      {edited && original && (
                        <span
                          className="ml-1.5 align-middle text-[10px] uppercase tracking-wide text-[color:var(--color-text-dim)]"
                          title={`Your original wording: ${original}`}
                        >
                          edited
                        </span>
                      )}
                    </div>
                    {edited && original && (
                      <div className="mt-0.5 hidden text-xs text-[color:var(--color-text-dim)] line-through opacity-60 group-focus-within:block group-hover:block">
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
