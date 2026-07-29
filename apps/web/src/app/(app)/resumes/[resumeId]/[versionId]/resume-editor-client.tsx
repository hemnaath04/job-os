"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  CheckCircle2,
  Download,
  Eye,
  FileCheck2,
  Github,
  Loader2,
  MessageSquareText,
  Plus,
  Save,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserRound,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { downloadPdf } from "@/lib/download";
import type {
  JsonResume,
  ResumeChatResponse,
  ResumeReviewIssue,
  ResumeReviewResult,
} from "@/lib/types";

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
  const [mode, setMode] = useState<"edit" | "preview">("edit");
  const [pendingProposal, setPendingProposal] =
    useState<ResumeChatResponse | null>(null);

  const versionQuery = useQuery({
    queryKey: ["resume-version", resumeId, versionId],
    queryFn: () => api.getVersion(resumeId, versionId),
  });
  const messagesQuery = useQuery({
    queryKey: ["resume-messages", resumeId, versionId],
    queryFn: () => api.listRevisionMessages(resumeId, versionId),
  });
  const previewQuery = useQuery({
    queryKey: ["resume-draft-preview", draft],
    queryFn: () => api.previewDraft(draft ?? {}),
    enabled: mode === "preview" && draft !== null,
  });

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
    onError: (error: Error) => toast.error(error.message),
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
    onError: (error: Error) => toast.error(error.message),
  });
  const finalize = useMutation({
    mutationFn: (force: boolean) =>
      api.finalizeVersion(resumeId, versionId, { force }),
    onSuccess: (outcome) => {
      queryClient.invalidateQueries({
        queryKey: ["resume-version", resumeId, versionId],
      });
      if (outcome.status !== "blocked") {
        toast.success("Resume finalized and stored");
        return;
      }
      // The review advises, it does not gate: an honest resume scores in the
      // seventies and the user must still be able to finalize it. Show what it
      // found and let them decide.
      const issues = outcome.review.issues
        .slice(0, 8)
        .map((issue) => `- ${issue.message}`)
        .join("\n");
      const proceed = window.confirm(
        `The review scored this ${Math.round(Number(outcome.review.score))}/100 ` +
          `and flagged ${outcome.review.issues.length} issue` +
          `${outcome.review.issues.length === 1 ? "" : "s"}:\n\n${issues}\n\n` +
          "Finalize anyway?",
      );
      if (proceed) finalize.mutate(true);
    },
    onError: (error: Error) => toast.error(parseFinalizeError(error.message)),
    onSettled: () => {
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
    onError: (error: Error) => toast.error(error.message),
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
    onError: (error: Error) => toast.error(error.message),
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
            <ModeButton active={mode === "edit"} onClick={() => setMode("edit")} icon={Save}>
              Edit
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
            {finalize.isPending ? "Finalizing…" : "Finalize"}
          </button>
          {(review.isPending || finalize.isPending) && (
            <span className="text-xs text-[color:var(--color-text-dim)]">
              Rendering the PDF and scoring the draft, up to a minute.
            </span>
          )}
          {/* A reviewed version has a rendered PDF even before it is final, so
              offer the download as soon as one exists. */}
          {!!downloadUrl && (
            <button
              onClick={() =>
                downloadPdf(downloadUrl, "Hemnaath_Balasubramani_Resume.pdf")
              }
              className="product-button product-button-secondary"
            >
              <Download className="size-4" />
              PDF
            </button>
          )}
        </div>
      </header>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.45fr)_minmax(340px,.55fr)]">
        {/* Not a <main>: the app shell already owns the page's main landmark,
            and nesting a second one leaves the page with no single primary. */}
        <div className="min-w-0">
          {mode === "preview" ? (
            <div className="product-panel min-h-[78dvh] overflow-hidden bg-[color:var(--color-surface-hover)]">
              {previewQuery.isLoading ? (
                <div className="loading-surface h-[78dvh]" />
              ) : previewQuery.isError ? (
                <div className="p-6 text-sm text-red-700">
                  The draft preview could not be rendered.
                </div>
              ) : (
                <iframe
                  title="Unsaved resume draft preview"
                  srcDoc={previewQuery.data}
                  sandbox=""
                  className="h-[78dvh] w-full"
                />
              )}
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
                <div
                  key={message.id}
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
    </div>
  );
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
              className="rounded-xl border border-[color:var(--color-border)] bg-black/10 p-4"
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
            <div key={`${String(item[nameKey])}-${index}`} className="rounded-xl border border-[color:var(--color-border)] bg-black/10 p-4">
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
          Run Review to check page count, ATS text, unsupported claims, GitHub evidence, and writing quality.
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
            {review.page_count} page · {review.text_selectable ? "ATS text verified" : "text issue"}
          </p>
        </div>
        <div className="text-2xl font-semibold">{Math.round(Number(review.score))}</div>
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
    <button onClick={onClick} className="rounded-lg p-2 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-rose)]/10 hover:text-[color:var(--color-rose)]" aria-label={label}>
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
    <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${final ? "border-[color:var(--color-mint)]/20 bg-[color:var(--color-mint)]/10 text-[color:var(--color-mint)]" : "border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-dim)]"}`}>
      {status.replaceAll("_", " ")}
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
