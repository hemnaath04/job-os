"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  Columns2,
  Download,
  Eye,
  FileCheck2,
  Github,
  Loader2,
  MessageSquareText,
  Pencil,
  Plus,
  Save,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { PdfPreviewPane } from "@/components/pdf-preview-pane";
import { reportFailure } from "@/lib/errors";
import { buildResumeFilename, downloadPdf } from "@/lib/download";
import { useDebouncedValue } from "@/lib/hooks/use-debounced-value";
import { versionStatusLabel } from "@/lib/types";
import type {
  BlockedClaim,
  JsonResume,
  ResumeChatResponse,
  ResumeReviewIssue,
  ResumeReviewResult,
  ResumeVersion,
} from "@/lib/types";

/**
 * "Firstname Lastname Company Role.pdf" instead of a name that never changed
 * across every resume this account ever downloaded. An uploaded file's own
 * name is trusted as-is (it's the artifact the user built, not something
 * this app produced). Otherwise the company/role comes from whichever job
 * this version was tailored against -- best-effort: an unreachable
 * application/job degrades to just the person's name rather than blocking
 * the download itself.
 */
async function resolveDownloadFilename(version: ResumeVersion): Promise<string> {
  if (version.source_filename) return version.source_filename;
  const person = version.json_resume?.basics?.name;
  let company: string | undefined;
  let role: string | undefined;
  try {
    if (version.spawned_from_application_id) {
      const applications = await api.listApplications();
      const application = applications.find(
        (a) => a.id === version.spawned_from_application_id,
      );
      company = application?.job.company?.name;
      role = application?.job.title;
    } else if (version.spawned_from_job_id) {
      const job = await api.getJob(version.spawned_from_job_id);
      company = job.company?.name;
      role = job.title;
    }
  } catch {
    // Best-effort naming only -- an unreachable job/application shouldn't
    // block the download itself.
  }
  return buildResumeFilename([person, company, role]);
}

export default function ResumeEditorClient({
  resumeId,
  versionId,
}: {
  resumeId: string;
  versionId: string;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<JsonResume | null>(null);
  const [chat, setChat] = useState("");
  // Opening a resume from the library is "look at this", not "change this" --
  // landing in the full editor by default made every click feel like it put
  // something at risk. Preview first; Edit and Split are one click away in
  // the toolbar below for whoever actually means to change it.
  const [mode, setMode] = useState<"edit" | "split" | "preview">("preview");
  const [pendingProposal, setPendingProposal] =
    useState<ResumeChatResponse | null>(null);
  // The blocked finalize review, shown in an in-app panel. Null when there is
  // nothing to decide on.
  const [finalizeReview, setFinalizeReview] =
    useState<ResumeReviewResult | null>(null);
  // True once a finalize has rendered and attached the PDF but is still running
  // the review, so the progress banner can offer the download mid-flight.
  const [finalizePdfReady, setFinalizePdfReady] = useState(false);

  const versionQuery = useQuery({
    queryKey: ["resume-version", resumeId, versionId],
    queryFn: () => api.getVersion(resumeId, versionId),
  });
  const messagesQuery = useQuery({
    queryKey: ["resume-messages", resumeId, versionId],
    queryFn: () => api.listRevisionMessages(resumeId, versionId),
  });
  // Debounced so Split mode's live preview compiles once typing pauses, not
  // on every keystroke; switching into Preview/Split without having just
  // typed anything sees no delay, since the debounced value already equals
  // the live one by then.
  const debouncedDraft = useDebouncedValue(draft, 700);
  const previewQuery = useQuery({
    queryKey: ["resume-draft-preview", debouncedDraft],
    queryFn: () => api.previewDraft(debouncedDraft ?? {}),
    enabled: mode !== "edit" && debouncedDraft !== null,
  });

  // `previewDraft` now hands back an object URL to a real, blob-backed PDF,
  // not inline HTML -- one is minted per render and browsers don't reclaim
  // it on their own. Revoking the *previous* URL (not the current one, which
  // the iframe below still needs) on every change and on unmount is what
  // keeps a long editing session from leaking one blob per keystroke-driven
  // re-preview. `revokeObjectURL` on a plain data: URL (the plain-HTML
  // fallback) is a harmless no-op, so this doesn't need to know which kind
  // of URL it got back.
  const previousPreviewUrl = useRef<string | null>(null);
  useEffect(() => {
    const current = previewQuery.data ?? null;
    if (previousPreviewUrl.current && previousPreviewUrl.current !== current) {
      URL.revokeObjectURL(previousPreviewUrl.current);
    }
    previousPreviewUrl.current = current;
  }, [previewQuery.data]);
  useEffect(() => {
    return () => {
      if (previousPreviewUrl.current) URL.revokeObjectURL(previousPreviewUrl.current);
    };
  }, []);

  useEffect(() => {
    if (versionQuery.data) {
      setDraft(structuredClone(versionQuery.data.json_resume));
      setPendingProposal(null);
    }
  }, [versionQuery.data]);

  const save = useMutation({
    mutationFn: () =>
      api.editVersion(resumeId, versionId, draft ?? {}, "Manual editor update"),
    onSuccess: (version) => {
      toast.success("New revision saved");
      router.replace(`/resumes/${resumeId}/${version.id}`);
    },
    onError: (error: Error) => reportFailure("save this revision", error),
  });
  const review = useMutation({
    mutationFn: () => api.reviewVersion(resumeId, versionId),
    onSuccess: (result) => {
      toast[result.passed ? "success" : "warning"](
        result.passed ? "Quality gate passed" : "Review found changes",
      );
      queryClient.invalidateQueries({
        queryKey: ["resume-version", resumeId, versionId],
      });
    },
    onError: (error: Error) => reportFailure("run the review", error),
  });
  const finalize = useMutation({
    mutationFn: (force: boolean) =>
      api.finalizeVersion(resumeId, versionId, {
        force,
        // Fires once the PDF is rendered and attached, before the slower review
        // finishes. Refetch so Download lights up at ~17s, and flag it so the
        // progress banner offers the file while the review keeps running.
        onPdfReady: () => {
          setFinalizePdfReady(true);
          queryClient.invalidateQueries({
            queryKey: ["resume-version", resumeId, versionId],
          });
        },
      }),
    onMutate: () => setFinalizePdfReady(false),
    onSuccess: (outcome) => {
      queryClient.invalidateQueries({
        queryKey: ["resume-version", resumeId, versionId],
      });
      if (outcome.status === "blocked") {
        // The review advises, it does not gate: an honest resume scores in the
        // seventies and the user must still be able to finalize it. Surface the
        // findings in an in-app panel that can render severity and an action,
        // rather than a native browser dialog that can render neither.
        setFinalizeReview(outcome.review);
        return;
      }
      // Finalized: the PDF is rendered and stored. Close the panel and let the
      // success banner offer the download, so the click never feels like it
      // did nothing.
      setFinalizeReview(null);
      toast.success("Resume finalized. Your PDF is ready to download.");
    },
    onError: (error: Error) =>
      reportFailure("finalize this resume", error, parseFinalizeError(error.message)),
    onSettled: () => {
      setFinalizePdfReady(false);
      queryClient.invalidateQueries({
        queryKey: ["resume-version", resumeId, versionId],
      });
    },
  });
  const chatEdit = useMutation({
    mutationFn: (message: string) =>
      api.chatEditVersion(resumeId, versionId, message, false),
    onSuccess: (response) => {
      toast.success("Suggestions ready to review");
      setChat("");
      setPendingProposal(response);
      queryClient.invalidateQueries({
        queryKey: ["resume-messages", resumeId, versionId],
      });
    },
    onError: (error: Error) => reportFailure("get suggestions", error),
  });
  const applyProposal = useMutation({
    mutationFn: (proposalId: string) =>
      api.applyRevisionProposal(resumeId, versionId, proposalId),
    onSuccess: (response) => {
      toast.success(
        response.review?.passed
          ? "Revision applied and quality gate passed"
          : "Revision applied. Review found more changes.",
      );
      setPendingProposal(null);
      if (response.version) {
        router.replace(`/resumes/${resumeId}/${response.version.id}`);
      }
    },
    onError: (error: Error) => reportFailure("apply that revision", error),
  });

  const reviewResult = useMemo(
    () => review.data ?? versionQuery.data?.review_report ?? null,
    [review.data, versionQuery.data?.review_report],
  );
  const isDirty = useMemo(
    () =>
      draft !== null &&
      versionQuery.data !== undefined &&
      JSON.stringify(draft) !== JSON.stringify(versionQuery.data.json_resume),
    [draft, versionQuery.data],
  );
  const improveMessage = reviewResult
    ? [
        "Apply every supported quality-review suggestion below. Keep the resume one page,",
        "preserve verified facts, and do not add unsupported claims.",
        ...reviewResult.issues.map(
          (issue) => `${issue.severity.toUpperCase()}: ${issue.message}`,
        ),
      ].join("\n")
    : "";

  if (versionQuery.isLoading || !draft) {
    return <div className="workspace-page"><div className="loading-surface" /></div>;
  }
  if (versionQuery.isError || !versionQuery.data) {
    return (
      <div className="workspace-page">
        <div className="product-empty-state">
          <AlertTriangle className="size-6" />
          <h1>Resume version unavailable</h1>
          <p>Return to the resume library and choose another version.</p>
        </div>
      </div>
    );
  }

  const version = versionQuery.data;
  const downloadUrl = api.downloadVersionUrl(resumeId, versionId);
  // A downloadable PDF exists once the render has uploaded one. The precise
  // signal is the stored file id the download URL resolves to (pdf_file_id on
  // Appwrite, pdf_r2_key on the legacy path); both a blocked and a finalized
  // outcome attach it, so it is set well before the version is marked final.
  const storedFileId =
    (version as ResumeVersion & { pdf_file_id?: string | null }).pdf_file_id ??
    version.pdf_r2_key;
  const hasRenderedPdf = Boolean(storedFileId) || version.status === "final";
  const downloadResume = async () => {
    downloadPdf(downloadUrl, await resolveDownloadFilename(version));
  };

  return (
    <div className="workspace-page max-w-[1720px]">
      <header className="mb-4 flex flex-col gap-4 border-b border-[color:var(--color-border)] pb-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <Link
            href="/resumes"
            className="rounded-lg border border-[color:var(--color-border)] p-2 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-surface-2)] hover:text-[color:var(--color-text)]"
            aria-label="Back to resumes"
          >
            <ArrowLeft className="size-4" />
          </Link>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-xl font-semibold tracking-tight">
                Resume editor
              </h1>
              <StatusBadge status={version.status} />
              {isDirty && (
                <span className="text-[11px] font-medium text-[color:var(--color-amber)]">
                  Unsaved changes
                </span>
              )}
            </div>
            <p className="mt-1 text-xs text-[color:var(--color-text-dim)]">
              Every save creates a recoverable revision. The master is never overwritten.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <div
            role="group"
            aria-label="Editor mode"
            className="flex rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-0.5"
          >
            <ModeButton active={mode === "edit"} onClick={() => setMode("edit")} icon={Pencil}>
              Edit
            </ModeButton>
            <ModeButton active={mode === "split"} onClick={() => setMode("split")} icon={Columns2}>
              Split
            </ModeButton>
            <ModeButton active={mode === "preview"} onClick={() => setMode("preview")} icon={Eye}>
              Preview
            </ModeButton>
          </div>
          <button
            onClick={() => review.mutate()}
            disabled={review.isPending || isDirty}
            className="product-button product-button-secondary disabled:opacity-50"
          >
            {review.isPending ? <Loader2 className="size-4 animate-spin" /> : <ShieldCheck className="size-4" />}
            {review.isPending ? "Reviewing…" : "Review"}
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending}
            className="product-button product-button-secondary disabled:opacity-50"
          >
            {save.isPending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
            Save revision
          </button>
          <button
            onClick={() => finalize.mutate(false)}
            disabled={finalize.isPending || isDirty}
            className="product-button product-button-primary disabled:opacity-50"
          >
            {finalize.isPending ? <Loader2 className="size-4 animate-spin" /> : <FileCheck2 className="size-4" />}
            {finalize.isPending
              ? "Finalizing…"
              : version.status === "final"
                ? "Re-finalize & render PDF"
                : "Finalize & render PDF"}
          </button>
          {review.isPending && (
            <span className="self-center text-xs text-[color:var(--color-text-dim)]">
              Scoring the draft, up to a minute.
            </span>
          )}
        </div>
      </header>

      <FinalizeStatus
        status={version.status}
        finalizing={finalize.isPending && finalizeReview === null}
        pdfReady={finalizePdfReady}
        hasRenderedPdf={hasRenderedPdf}
        onDownload={downloadResume}
      />

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,.55fr)]">
        {/* Not a <main>: the app shell already owns the page's main landmark,
            and nesting a second one leaves the page with no single primary. */}
        <div className="min-w-0">
          {mode === "preview" ? (
            <div className="product-panel h-[78dvh]">
              <PdfPreviewPane query={previewQuery} />
            </div>
          ) : mode === "split" ? (
            <div className="grid gap-3 lg:grid-cols-2">
              <StructuredEditor value={draft} onChange={setDraft} />
              <div className="product-panel h-[78dvh] lg:sticky lg:top-16">
                <PdfPreviewPane query={previewQuery} />
              </div>
            </div>
          ) : (
            <StructuredEditor value={draft} onChange={setDraft} />
          )}
        </div>

        <aside className="space-y-3">
          <QualityPanel
            review={reviewResult}
            improving={chatEdit.isPending}
            onImprove={() => chatEdit.mutate(improveMessage)}
          />
          <section className="product-panel overflow-hidden xl:sticky xl:top-16">
            <div className="border-b border-[color:var(--color-border)] px-4 py-3">
              <div className="flex items-center gap-2">
                <MessageSquareText className="size-4 text-[color:var(--color-kiwi)]" />
                <h2 className="text-sm font-semibold">Edit with AI</h2>
              </div>
              <p className="mt-1 text-xs leading-5 text-[color:var(--color-text-dim)]">
                Ask for a rewrite, reorder, removal, or project update. The result is checked against verified facts and GitHub before it is saved.
              </p>
            </div>
            {/* A log, so a reply that arrives after the request is announced
                instead of appearing silently. */}
            <div
              role="log"
              aria-label="Edit conversation"
              className="max-h-[42dvh] space-y-3 overflow-y-auto p-4"
            >
              {(messagesQuery.data ?? []).map((message) => (
                <div key={message.id} className="space-y-2">
                  <div
                    className={`flex gap-2 ${message.role === "user" ? "justify-end" : ""}`}
                  >
                    {message.role === "assistant" && (
                      <span className="product-icon size-7 shrink-0"><Bot className="size-3.5" /></span>
                    )}
                    <div
                      className={`max-w-[88%] rounded-xl px-3 py-2 text-xs leading-5 ${
                        message.role === "user"
                          ? "bg-[color:var(--color-kiwi)]/12 text-[color:var(--color-text)]"
                          : "border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-muted)]"
                      }`}
                    >
                      {message.content}
                    </div>
                  </div>
                  {message.role === "assistant" && !!message.blocked_claims?.length && (
                    <BlockedClaimsNotice claims={message.blocked_claims} />
                  )}
                  {/* A proposal reached after navigating back (or after a reload)
                      is only in the log, not the live panel below, so give it a
                      way to still be applied rather than stranding the result. */}
                  {message.role === "assistant" &&
                    !message.applied &&
                    !!message.proposed_json_resume &&
                    pendingProposal?.proposal_id !== message.id && (
                      <button
                        onClick={() => applyProposal.mutate(message.id)}
                        disabled={applyProposal.isPending}
                        className="product-button product-button-secondary w-full justify-center disabled:opacity-50"
                      >
                        {applyProposal.isPending ? (
                          <Loader2 className="size-4 animate-spin" />
                        ) : (
                          <CheckCircle2 className="size-4" />
                        )}
                        Apply this edit and review
                      </button>
                    )}
                </div>
              ))}
              {!messagesQuery.data?.length && (
                <div className="py-6 text-center">
                  <Sparkles className="mx-auto size-5 text-[color:var(--color-text-dim)]" />
                  <p className="mt-2 text-xs text-[color:var(--color-text-dim)]">
                    Try “Make the BedRocked bullets more backend-focused.”
                  </p>
                </div>
              )}
              {pendingProposal?.proposal_id && (
                <ProposalPanel
                  current={version.json_resume}
                  proposal={pendingProposal}
                  applying={applyProposal.isPending}
                  onApply={() =>
                    applyProposal.mutate(pendingProposal.proposal_id as string)
                  }
                  onDiscard={() => setPendingProposal(null)}
                />
              )}
            </div>
            <div className="border-t border-[color:var(--color-border)] p-3">
              <label htmlFor="ai-edit-request" className="sr-only">
                Describe the edit you want
              </label>
              <textarea
                id="ai-edit-request"
                value={chat}
                onChange={(event) => setChat(event.target.value)}
                placeholder="Describe the edit you want…"
                rows={3}
                className="field-control resize-none"
              />
              <button
                onClick={() => chatEdit.mutate(chat)}
                disabled={chatEdit.isPending || chat.trim().length < 2}
                className="product-button product-button-primary mt-2 w-full disabled:opacity-50"
              >
                {chatEdit.isPending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                Suggest verified edits
              </button>
            </div>
          </section>
        </aside>
      </div>

      <FinalizeReviewDialog
        review={finalizeReview}
        finalizing={finalize.isPending}
        onFinalize={() => finalize.mutate(true)}
        onCancel={() => setFinalizeReview(null)}
      />
    </div>
  );
}

function FinalizeStatus({
  status,
  finalizing,
  pdfReady,
  hasRenderedPdf,
  onDownload,
}: {
  status: string;
  finalizing: boolean;
  pdfReady: boolean;
  hasRenderedPdf: boolean;
  onDownload: () => void;
}) {
  if (finalizing) {
    // Two phases: the PDF renders first (~17s), then the review scores it
    // (~80s). Once the PDF is attached, say so and offer the download instead
    // of making the user wait out the review for a file that already exists.
    return (
      <div className="mb-4 flex flex-col gap-3 rounded-[var(--radius-card)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-3.5 text-sm sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2.5 text-[color:var(--color-text-muted)]">
          <Loader2 className="size-4 shrink-0 animate-spin text-[color:var(--color-accent-ink)]" />
          <span>
            {pdfReady
              ? "Your PDF is ready to download. Scoring the draft for the quality review, up to a minute more."
              : "Finalizing: rendering your one-page PDF, then scoring the draft. This can take up to a minute."}
          </span>
        </div>
        {pdfReady && (
          <button
            onClick={onDownload}
            className="product-button product-button-secondary shrink-0 justify-center"
          >
            <Download className="size-4" />
            Download PDF
          </button>
        )}
      </div>
    );
  }
  if (status === "final") {
    return (
      <div className="notice notice-positive mb-4 flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2.5">
          <CheckCircle2 className="size-5 shrink-0" />
          <div>
            <p className="text-sm font-semibold">Resume finalized</p>
            <p className="notice-detail text-xs leading-5">
              Your one-page PDF is rendered and stored. Download it here.
            </p>
          </div>
        </div>
        <button
          onClick={onDownload}
          className="product-button product-button-primary shrink-0 justify-center"
        >
          <Download className="size-4" />
          Download PDF
        </button>
      </div>
    );
  }
  if (hasRenderedPdf) {
    return (
      <div className="mb-4 flex flex-col gap-2 rounded-[var(--radius-card)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-3 text-xs sm:flex-row sm:items-center sm:justify-between">
        <span className="text-[color:var(--color-text-dim)]">
          A PDF from your last review is ready. Finalize to lock this version as
          final.
        </span>
        <button
          onClick={onDownload}
          className="product-button product-button-secondary shrink-0 justify-center"
        >
          <Download className="size-4" />
          Download PDF
        </button>
      </div>
    );
  }
  return null;
}

/**
 * The finalize review, shown in-app instead of through a native browser
 * confirm. The review advises, it does not gate: an honest one-page resume
 * routinely scores in the seventies, so the user reads every finding, then
 * finalizes anyway or returns to editing. Severity uses the same status hues as
 * the rest of the app, with the most serious findings first.
 */
function FinalizeReviewDialog({
  review,
  finalizing,
  onFinalize,
  onCancel,
}: {
  review: ResumeReviewResult | null;
  finalizing: boolean;
  onFinalize: () => void;
  onCancel: () => void;
}) {
  const issues = review
    ? [...review.issues].sort(
        (a, b) => severityRank(a.severity) - severityRank(b.severity),
      )
    : [];
  return (
    <Dialog.Root
      open={review !== null}
      onOpenChange={(next) => {
        // Don't let a stray Escape or backdrop click abandon a finalize that is
        // already in flight.
        if (!next && !finalizing) onCancel();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-lg -translate-x-1/2 -translate-y-1/2 px-4">
          <div className="glass max-h-[85vh] overflow-y-auto rounded-[var(--radius-card)] p-6">
            {review && (
              <>
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <Dialog.Title className="flex items-center gap-2 text-lg font-semibold">
                      <ShieldCheck className="size-5 text-[color:var(--color-amber-ink)]" />
                      Review before finalizing
                    </Dialog.Title>
                    <Dialog.Description className="mt-1 text-sm leading-5 text-[color:var(--color-text-muted)]">
                      The review advises, it does not block. Read what it found,
                      then finalize anyway or keep editing.
                    </Dialog.Description>
                  </div>
                  <Dialog.Close
                    aria-label="Close"
                    disabled={finalizing}
                    className="grid size-8 shrink-0 place-items-center rounded-md text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)] disabled:opacity-50"
                  >
                    <X className="size-4" aria-hidden="true" />
                  </Dialog.Close>
                </div>

                <div className="mt-4 flex items-center gap-3 rounded-[var(--radius-card)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-3">
                  <div className="text-3xl font-semibold leading-none">
                    {formatScore(review.score)}
                  </div>
                  <div className="text-xs leading-5 text-[color:var(--color-text-dim)]">
                    <span className="font-medium text-[color:var(--color-text-muted)]">
                      Quality score
                    </span>{" "}
                    out of 100
                    <br />
                    {review.page_count} page{review.page_count === 1 ? "" : "s"}{" "}
                    · {review.text_selectable ? "Selectable text" : "Selectable text issue"}
                  </div>
                  <div className="ml-auto shrink-0 text-right text-xs text-[color:var(--color-text-dim)]">
                    {review.issues.length} finding
                    {review.issues.length === 1 ? "" : "s"}
                  </div>
                </div>

                {issues.length > 0 && (
                  <ul className="mt-4 space-y-2">
                    {issues.map((issue) => (
                      <FinalizeIssueRow
                        key={`${issue.code}-${issue.message}`}
                        issue={issue}
                      />
                    ))}
                  </ul>
                )}

                <p className="mt-4 text-xs leading-5 text-[color:var(--color-text-dim)]">
                  Finalizing renders and stores the PDF as it stands now. Keep
                  editing to address these first, or finalize anyway if they are
                  intentional.
                </p>

                <div className="mt-4 flex justify-end gap-2">
                  <button
                    onClick={onCancel}
                    disabled={finalizing}
                    className="product-button product-button-secondary disabled:opacity-50"
                  >
                    Keep editing
                  </button>
                  <button
                    onClick={onFinalize}
                    disabled={finalizing}
                    className="product-button product-button-primary disabled:opacity-50"
                  >
                    {finalizing ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <FileCheck2 className="size-4" />
                    )}
                    {finalizing ? "Finalizing…" : "Finalize anyway"}
                  </button>
                </div>
              </>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const SEVERITY_RANK: Record<string, number> = {
  blocking: 0,
  warning: 1,
  suggestion: 2,
};

function severityRank(severity: string): number {
  return SEVERITY_RANK[severity] ?? 3;
}

function FinalizeIssueRow({ issue }: { issue: ResumeReviewIssue }) {
  const pill =
    issue.severity === "blocking"
      ? "bg-[color:var(--color-rose)]/15 text-[color:var(--color-rose-ink)]"
      : issue.severity === "warning"
        ? "bg-[color:var(--color-amber)]/15 text-[color:var(--color-amber-ink)]"
        : "bg-[color:var(--color-surface-2)] text-[color:var(--color-text-dim)]";
  return (
    <li className="rounded-[var(--radius-nested)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <span
          className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${pill}`}
        >
          {issue.severity}
        </span>
        {issue.section && (
          <span className="text-[10px] font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
            {issue.section}
          </span>
        )}
      </div>
      <p className="mt-1.5 text-xs leading-5 text-[color:var(--color-text)]">
        {issue.message}
      </p>
    </li>
  );
}

function formatScore(score: string | number | null | undefined): string {
  const value = Number(score);
  return Number.isFinite(value) ? String(Math.round(value)) : "N/A";
}

function ProposalPanel({
  current,
  proposal,
  applying,
  onApply,
  onDiscard,
}: {
  current: JsonResume;
  proposal: ResumeChatResponse;
  applying: boolean;
  onApply: () => void;
  onDiscard: () => void;
}) {
  const sections = Object.keys(proposal.proposed_json_resume ?? {}).filter(
    (key) =>
      JSON.stringify(current[key as keyof JsonResume]) !==
      JSON.stringify(
        proposal.proposed_json_resume?.[key as keyof JsonResume],
      ),
  );
  return (
    <div className="rounded-xl border border-[color:var(--color-kiwi)]/20 bg-[color:var(--color-kiwi)]/[0.06] p-3">
      <div className="flex items-center gap-2 text-xs font-semibold text-[color:var(--color-text)]">
        <Sparkles className="size-3.5 text-[color:var(--color-kiwi)]" />
        Review before applying
      </div>
      <p className="mt-2 text-xs leading-5 text-[color:var(--color-text-muted)]">{proposal.message}</p>
      {!!proposal.blocked_claims?.length && (
        <BlockedClaimsNotice claims={proposal.blocked_claims} />
      )}
      {sections.length > 0 && (
        <p className="mt-2 text-[11px] text-[color:var(--color-text-dim)]">
          Changes: {sections.join(", ")}
        </p>
      )}
      {proposal.suggestions.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-4 text-[11px] leading-4 text-[color:var(--color-text-dim)]">
          {proposal.suggestions.slice(0, 4).map((suggestion) => (
            <li key={suggestion}>{suggestion}</li>
          ))}
        </ul>
      )}
      <div className="mt-3 grid grid-cols-2 gap-2">
        <button
          onClick={onDiscard}
          disabled={applying}
          className="product-button product-button-secondary justify-center disabled:opacity-50"
        >
          Discard
        </button>
        <button
          onClick={onApply}
          disabled={applying}
          className="product-button product-button-primary justify-center disabled:opacity-50"
        >
          {applying ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <CheckCircle2 className="size-4" />
          )}
          Apply and review
        </button>
      </div>
    </div>
  );
}

/**
 * The claims the revise guard left out because no verified Profile fact backs
 * their numbers. Deliberately calm, not an error: the rest of the edit applied,
 * and this explains exactly what did not and how to keep it. Each row names the
 * offending number, the sentence it was in, why it was held back, and the
 * remedy, with one link to the place that fixes all of them.
 */
function BlockedClaimsNotice({ claims }: { claims: BlockedClaim[] }) {
  if (!claims.length) return null;
  return (
    <div className="notice notice-caution p-3 text-xs">
      <div className="flex items-center gap-1.5 font-semibold">
        <ShieldCheck className="size-3.5 shrink-0" />
        <span>
          {claims.length} claim{claims.length === 1 ? "" : "s"} left out until verified
        </span>
      </div>
      <p className="notice-detail mt-1 leading-5">
        The rest of the edit is applied. These introduced numbers your Profile does
        not have yet, so the guard held them back rather than inventing them.
      </p>
      <ul className="mt-2.5 space-y-2">
        {claims.map((claim, index) => (
          <li
            key={index}
            className="rounded-[var(--radius-nested)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] p-2.5"
          >
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="rounded-full bg-[color:var(--color-amber)]/15 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-[color:var(--color-amber-ink)]">
                {claim.metric}
              </span>
              <span className="text-[10px] font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
                left out
              </span>
            </div>
            <p className="mt-1.5 leading-5 text-[color:var(--color-text)]">
              &ldquo;{claim.text}&rdquo;
            </p>
            <p className="mt-1 leading-5 text-[color:var(--color-text-muted)]">
              {claim.reason}
            </p>
            <p className="mt-1 leading-5 text-[color:var(--color-text-muted)]">
              {claim.remedy}
            </p>
          </li>
        ))}
      </ul>
      <Link
        href="/profile"
        className="mt-2.5 inline-flex items-center gap-1 font-medium text-[color:var(--color-accent-ink)] hover:underline"
      >
        <ArrowUpRight className="size-3" /> Open Profile to add verified facts
      </Link>
    </div>
  );
}

function StructuredEditor({
  value,
  onChange,
}: {
  value: JsonResume;
  onChange: (value: JsonResume) => void;
}) {
  const basics = value.basics ?? {};
  const location = basics.location ?? {};
  const updateBasics = (key: string, next: string) =>
    onChange({ ...value, basics: { ...basics, [key]: next } });
  const updateProfile = (network: string, url: string) => {
    const profiles = [...(basics.profiles ?? [])];
    const index = profiles.findIndex(
      (profile) => profile.network.toLowerCase() === network.toLowerCase(),
    );
    const nextProfile = { network, username: "", url };
    if (index >= 0) profiles[index] = { ...profiles[index], url };
    else profiles.push(nextProfile);
    onChange({ ...value, basics: { ...basics, profiles } });
  };

  return (
    <div className="space-y-3">
      <EditorSection title="Contact and summary" icon={UserRound}>
        <div className="grid gap-3 sm:grid-cols-2">
          {/* Right keyboard on a phone, and the browser can fill these. */}
          <Field label="Name" autoComplete="name" value={basics.name ?? ""} onChange={(next) => updateBasics("name", next)} />
          <Field label="Email" type="email" autoComplete="email" value={basics.email ?? ""} onChange={(next) => updateBasics("email", next)} />
          <Field label="Phone" type="tel" autoComplete="tel" value={basics.phone ?? ""} onChange={(next) => updateBasics("phone", next)} />
          <Field label="Portfolio" type="url" autoComplete="url" value={basics.url ?? ""} onChange={(next) => updateBasics("url", next)} />
          <Field
            label="City"
            value={location.city ?? ""}
            onChange={(next) =>
              onChange({
                ...value,
                basics: { ...basics, location: { ...location, city: next } },
              })
            }
          />
          <Field
            label="State"
            value={location.region ?? ""}
            onChange={(next) =>
              onChange({
                ...value,
                basics: { ...basics, location: { ...location, region: next } },
              })
            }
          />
          <Field
            label="GitHub"
            value={
              basics.profiles?.find(
                (profile) => profile.network.toLowerCase() === "github",
              )?.url ?? ""
            }
            onChange={(next) => updateProfile("GitHub", next)}
          />
          <Field
            label="LinkedIn"
            value={
              basics.profiles?.find(
                (profile) => profile.network.toLowerCase() === "linkedin",
              )?.url ?? ""
            }
            onChange={(next) => updateProfile("LinkedIn", next)}
          />
        </div>
        <TextArea label="Summary" value={basics.summary ?? ""} onChange={(next) => updateBasics("summary", next)} />
      </EditorSection>

      <EducationEditor
        items={value.education ?? []}
        onChange={(education) => onChange({ ...value, education })}
      />
      <EntryEditor
        title="Professional experience"
        items={value.work ?? []}
        onChange={(items) => onChange({ ...value, work: items })}
        nameKey="name"
        roleKey="position"
        allowAdd
      />
      <EntryEditor
        title="Projects"
        items={value.projects ?? []}
        onChange={(items) => onChange({ ...value, projects: items })}
        nameKey="name"
        roleKey="description"
        allowAdd
      />

      <EditorSection title="Technical skills" icon={Sparkles}>
        <div className="space-y-3">
          {(value.skills ?? []).map((group, index) => (
            <div key={`${group.name}-${index}`} className="grid gap-2 sm:grid-cols-[180px_1fr_auto]">
              <input
                aria-label={`Skill group ${index + 1}`}
                className="field-control"
                value={group.name}
                onChange={(event) => {
                  const skills = [...(value.skills ?? [])];
                  skills[index] = { ...group, name: event.target.value };
                  onChange({ ...value, skills });
                }}
              />
              <input
                aria-label={`Skills in ${group.name}`}
                className="field-control"
                value={group.keywords.join(", ")}
                onChange={(event) => {
                  const skills = [...(value.skills ?? [])];
                  skills[index] = {
                    ...group,
                    keywords: event.target.value.split(",").map((item) => item.trim()).filter(Boolean),
                  };
                  onChange({ ...value, skills });
                }}
              />
              <RemoveButton
                label={`Remove ${group.name}`}
                onClick={() =>
                  onChange({ ...value, skills: (value.skills ?? []).filter((_, itemIndex) => itemIndex !== index) })
                }
              />
            </div>
          ))}
          <button
            onClick={() =>
              onChange({
                ...value,
                skills: [...(value.skills ?? []), { name: "Skills", keywords: [] }],
              })
            }
            className="product-button product-button-secondary"
          >
            <Plus className="size-3.5" /> Add skill group
          </button>
        </div>
      </EditorSection>
      <CertificateEditor
        items={value.certificates ?? []}
        onChange={(certificates) => onChange({ ...value, certificates })}
      />
      <LanguageEditor
        items={value.languages ?? []}
        onChange={(languages) => onChange({ ...value, languages })}
      />
    </div>
  );
}

function EducationEditor({
  items,
  onChange,
}: {
  items: NonNullable<JsonResume["education"]>;
  onChange: (items: NonNullable<JsonResume["education"]>) => void;
}) {
  return (
    <EditorSection title="Education" icon={FileCheck2}>
      <div className="space-y-3">
        {items.map((item, index) => {
          const update = (key: string, next: unknown) => {
            const copy = [...items];
            copy[index] = { ...item, [key]: next };
            onChange(copy);
          };
          return (
            <div
              key={`${item.institution}-${index}`}
              className="rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-4"
            >
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Institution" value={item.institution ?? ""} onChange={(next) => update("institution", next)} />
                <Field label="Degree" value={item.studyType ?? ""} onChange={(next) => update("studyType", next)} />
                <Field label="Area" value={item.area ?? ""} onChange={(next) => update("area", next)} />
                <Field label="Location" value={item.location ?? ""} onChange={(next) => update("location", next)} />
                <Field label="Start date" value={item.startDate ?? ""} onChange={(next) => update("startDate", next)} />
                <Field label="End date" value={item.endDate ?? ""} onChange={(next) => update("endDate", next)} />
              </div>
              <TextArea
                label="Relevant coursework, one per line"
                value={(item.courses ?? []).join("\n")}
                onChange={(next) =>
                  update(
                    "courses",
                    next.split("\n").map((line) => line.trim()).filter(Boolean),
                  )
                }
              />
              <div className="mt-3 flex justify-end">
                <RemoveButton
                  label={`Remove ${item.institution ?? "education entry"}`}
                  onClick={() => onChange(items.filter((_, i) => i !== index))}
                />
              </div>
            </div>
          );
        })}
        <button
          onClick={() =>
            onChange([
              ...items,
              { institution: "New institution", courses: [] },
            ])
          }
          className="product-button product-button-secondary"
        >
          <Plus className="size-3.5" /> Add education
        </button>
      </div>
    </EditorSection>
  );
}

function CertificateEditor({
  items,
  onChange,
}: {
  items: NonNullable<JsonResume["certificates"]>;
  onChange: (items: NonNullable<JsonResume["certificates"]>) => void;
}) {
  return (
    <EditorSection title="Certifications" icon={ShieldCheck}>
      <div className="space-y-2">
        {items.map((item, index) => (
          <div key={`${item.name}-${index}`} className="grid gap-2 sm:grid-cols-[1fr_1fr_150px_auto]">
            <Field label="Name" value={item.name} onChange={(next) => {
              const copy = [...items]; copy[index] = { ...item, name: next }; onChange(copy);
            }} />
            <Field label="Issuer" value={item.issuer ?? ""} onChange={(next) => {
              const copy = [...items]; copy[index] = { ...item, issuer: next }; onChange(copy);
            }} />
            <Field label="Date" value={item.date ?? ""} onChange={(next) => {
              const copy = [...items]; copy[index] = { ...item, date: next }; onChange(copy);
            }} />
            <div className="flex items-end pb-1">
              <RemoveButton label={`Remove ${item.name}`} onClick={() => onChange(items.filter((_, i) => i !== index))} />
            </div>
          </div>
        ))}
        <button onClick={() => onChange([...items, { name: "New certification" }])} className="product-button product-button-secondary">
          <Plus className="size-3.5" /> Add certification
        </button>
      </div>
    </EditorSection>
  );
}

function LanguageEditor({
  items,
  onChange,
}: {
  items: NonNullable<JsonResume["languages"]>;
  onChange: (items: NonNullable<JsonResume["languages"]>) => void;
}) {
  return (
    <EditorSection title="Languages" icon={MessageSquareText}>
      <div className="space-y-2">
        {items.map((item, index) => (
          <div key={`${item.language}-${index}`} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
            <Field label="Language" value={item.language} onChange={(next) => {
              const copy = [...items]; copy[index] = { ...item, language: next }; onChange(copy);
            }} />
            <Field label="Fluency" value={item.fluency ?? ""} onChange={(next) => {
              const copy = [...items]; copy[index] = { ...item, fluency: next }; onChange(copy);
            }} />
            <div className="flex items-end pb-1">
              <RemoveButton label={`Remove ${item.language}`} onClick={() => onChange(items.filter((_, i) => i !== index))} />
            </div>
          </div>
        ))}
        <button onClick={() => onChange([...items, { language: "English", fluency: "" }])} className="product-button product-button-secondary">
          <Plus className="size-3.5" /> Add language
        </button>
      </div>
    </EditorSection>
  );
}

function EntryEditor({
  title,
  items,
  onChange,
  nameKey,
  roleKey,
  allowAdd = false,
}: {
  title: string;
  items: Record<string, unknown>[];
  onChange: (items: Record<string, unknown>[]) => void;
  nameKey: string;
  roleKey: string;
  allowAdd?: boolean;
}) {
  return (
    <EditorSection title={title} icon={Github}>
      <div className="space-y-3">
        {items.map((item, index) => {
          const highlights = Array.isArray(item.highlights)
            ? item.highlights.map(String)
            : [];
          const update = (key: string, next: unknown) => {
            const copy = [...items];
            copy[index] = { ...item, [key]: next };
            onChange(copy);
          };
          return (
            <div key={`${String(item[nameKey])}-${index}`} className="rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Organization or project" value={String(item[nameKey] ?? "")} onChange={(next) => update(nameKey, next)} />
                <Field label="Role or description" value={String(item[roleKey] ?? "")} onChange={(next) => update(roleKey, next)} />
                <Field label="Start date" value={String(item.startDate ?? "")} onChange={(next) => update("startDate", next)} />
                <Field label="End date" value={String(item.endDate ?? "")} onChange={(next) => update("endDate", next || null)} />
                <Field label="Evidence URL" value={String(item.url ?? "")} onChange={(next) => update("url", next || null)} />
              </div>
              <TextArea
                label="Bullets, one per line"
                value={highlights.join("\n")}
                onChange={(next) => update("highlights", next.split("\n").map((line) => line.trim()).filter(Boolean))}
              />
              {allowAdd && (
                <div className="mt-3 flex justify-end">
                  <RemoveButton label={`Remove ${String(item[nameKey] ?? "entry")}`} onClick={() => onChange(items.filter((_, itemIndex) => itemIndex !== index))} />
                </div>
              )}
            </div>
          );
        })}
        {allowAdd && (
          <button
            onClick={() => onChange([...items, { [nameKey]: "New project", [roleKey]: "", highlights: [] }])}
            className="product-button product-button-secondary"
          >
            <Plus className="size-3.5" /> Add entry
          </button>
        )}
      </div>
    </EditorSection>
  );
}

function QualityPanel({
  review,
  improving,
  onImprove,
}: {
  review: ResumeReviewResult | null;
  improving: boolean;
  onImprove: () => void;
}) {
  if (!review) {
    return (
      <section className="product-panel p-4">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <ShieldCheck className="size-4 text-[color:var(--color-kiwi)]" />
          Quality gate
        </div>
        <p className="mt-2 text-xs leading-5 text-[color:var(--color-text-dim)]">
          Run Review to check page count, selectable text, unsupported claims, GitHub evidence, and writing quality.
        </p>
      </section>
    );
  }
  return (
    <section className="product-panel p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold">
            {review.passed ? <CheckCircle2 className="size-4 text-[color:var(--color-mint)]" /> : <AlertTriangle className="size-4 text-[color:var(--color-amber)]" />}
            Quality gate
          </div>
          <p className="mt-1 text-xs text-[color:var(--color-text-dim)]">
            {review.page_count} page · {review.text_selectable ? "Selectable text" : "Selectable text issue"}
          </p>
        </div>
        <div className="text-2xl font-semibold">{formatScore(review.score)}</div>
      </div>
      {review.github_projects_checked.length > 0 && (
        <div className="mt-3 flex items-center gap-2 rounded-lg bg-[color:var(--color-surface-2)] px-3 py-2 text-[11px] text-[color:var(--color-text-dim)]">
          <Github className="size-3.5" />
          {review.github_projects_checked.length} GitHub README{review.github_projects_checked.length === 1 ? "" : "s"} checked
        </div>
      )}
      <div className="mt-3 space-y-2">
        {review.issues.slice(0, 5).map((issue) => <Issue key={`${issue.code}-${issue.message}`} issue={issue} />)}
      </div>
      {!review.passed && review.issues.length > 0 && (
        <button
          onClick={onImprove}
          disabled={improving}
          className="product-button product-button-primary mt-3 w-full disabled:opacity-50"
        >
          {improving ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Sparkles className="size-4" />
          )}
          {improving ? "Drafting suggestions…" : "Propose review fixes"}
        </button>
      )}
      {improving && (
        <p className="mt-2 text-xs text-[color:var(--color-text-dim)]">
          The editor is rewriting against your verified facts. This usually takes
          under a minute, and it reports an error rather than waiting forever.
        </p>
      )}
    </section>
  );
}

function Issue({ issue }: { issue: ResumeReviewIssue }) {
  return (
    <div className="rounded-lg border border-[color:var(--color-border)] px-3 py-2">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-[color:var(--color-text-dim)]">
        {issue.severity}
      </div>
      <p className="mt-1 text-xs leading-5 text-[color:var(--color-text-muted)]">{issue.message}</p>
    </div>
  );
}

function EditorSection({ title, icon: Icon, children }: { title: string; icon: typeof UserRound; children: React.ReactNode }) {
  return (
    <section className="product-panel">
      <div className="flex items-center gap-2 border-b border-[color:var(--color-border)] px-5 py-4">
        <Icon className="size-4 text-[color:var(--color-kiwi)]" />
        <h2 className="text-sm font-semibold">{title}</h2>
      </div>
      <div className="space-y-3 p-5">{children}</div>
    </section>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  autoComplete,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "email" | "tel" | "url";
  autoComplete?: string;
}) {
  return (
    <label className="block text-xs font-medium text-[color:var(--color-text-muted)]">
      {label}
      <input
        className="field-control mt-1.5"
        type={type}
        autoComplete={autoComplete}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

function TextArea({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="mt-3 block text-xs font-medium text-[color:var(--color-text-muted)]">
      {label}
      <textarea className="field-control mt-1.5 min-h-24 resize-y" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="rounded-lg p-2 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-rose)]/10 hover:text-[color:var(--color-rose-ink)]" aria-label={label}>
      <Trash2 className="size-4" />
    </button>
  );
}

function ModeButton({ active, onClick, icon: Icon, children }: { active: boolean; onClick: () => void; icon: typeof Save; children: React.ReactNode }) {
  return (
    <button onClick={onClick} aria-pressed={active} className={`inline-flex items-center gap-1.5 rounded-md px-3 py-2 text-xs transition ${active ? "bg-[color:var(--color-surface-hover)] text-[color:var(--color-text)]" : "text-[color:var(--color-text-dim)] hover:text-[color:var(--color-text)]"}`}>
      <Icon className="size-3.5" />
      {children}
    </button>
  );
}

function StatusBadge({ status }: { status: string }) {
  const final = status === "final";
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${final ? "border-[color:var(--color-mint)]/35 bg-[color:var(--color-mint)]/10 text-[color:var(--color-mint-ink)]" : "border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-dim)]"}`}>
      {versionStatusLabel(status)}
    </span>
  );
}

function parseFinalizeError(message: string) {
  try {
    const start = message.indexOf("{");
    const parsed = JSON.parse(message.slice(start)) as { detail?: { message?: string } };
    return parsed.detail?.message ?? "Final review found changes to make.";
  } catch {
    return message;
  }
}
