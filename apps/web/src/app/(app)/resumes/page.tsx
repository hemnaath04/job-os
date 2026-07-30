"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import {
  Archive,
  ArrowDownWideNarrow,
  CheckCircle2,
  ChevronRight,
  Crown,
  Download,
  FolderOpen,
  FileUp,
  FileText,
  LayoutTemplate,
  LibraryBig,
  Loader2,
  MessageSquareText,
  Plus,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { TemplatePicker } from "@/components/template-picker";
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";
import { reportFailure } from "@/lib/errors";
import { appwriteWorkspace } from "@/lib/appwrite/workspace";
import { downloadPdf } from "@/lib/download";
import { versionStatusLabel } from "@/lib/types";
import type {
  Resume,
  ResumeImportItem,
  ResumeTemplate,
  ResumeVersionSummary,
} from "@/lib/types";

/** Most recent activity on a resume, for sorting. Falls back to creation. */
function resumeTimestamp(resume: Resume): number {
  const parsed = Date.parse(resume.updated_at || resume.created_at);
  return Number.isFinite(parsed) ? parsed : 0;
}

type ResumeSort = "newest" | "oldest" | "name";

const RESUME_SORT_OPTIONS: { value: ResumeSort; label: string }[] = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "name", label: "Name A to Z" },
];

export default function ResumesPage() {
  // useSearchParams needs a Suspense boundary in a client component.
  return (
    <Suspense fallback={<div className="workspace-page max-w-6xl" />}>
      <ResumesInner />
    </Suspense>
  );
}

function ResumesInner() {
  const qc = useQueryClient();
  // /resumes?open={resumeId} lands from the tailor result so the resume holding
  // the version the user just generated is already expanded.
  const openId = useSearchParams().get("open");
  const { data: resumes = [], isLoading } = useQuery({
    queryKey: ["resumes"],
    queryFn: () => api.listResumes(),
  });
  const [sort, setSort] = useState<ResumeSort>("newest");
  const sorted = useMemo(() => {
    const rows = [...resumes];
    if (sort === "name") {
      return rows.sort((left, right) => left.name.localeCompare(right.name));
    }
    const direction = sort === "newest" ? -1 : 1;
    return rows.sort(
      (left, right) => (resumeTimestamp(left) - resumeTimestamp(right)) * direction,
    );
  }, [resumes, sort]);
  // Templates are their own rows holding a look, not tagged resumes, so every
  // resume is a source resume and the two lists come from two places.
  const { data: templates = [] } = useQuery({
    queryKey: ["templates"],
    queryFn: () => api.listTemplates(),
  });
  // Only resumes with a stored original document can yield a design, so ask once
  // and gate the gesture on the answer rather than promising and then failing.
  const { data: originals = {} } = useQuery({
    queryKey: ["resume-originals", resumes.map((r) => r.id).join(",")],
    enabled: resumes.length > 0,
    queryFn: async () => {
      const entries = await Promise.all(
        resumes.map(
          async (r) =>
            [r.id, !!(await appwriteWorkspace.findResumeOriginalDocument(r.id))] as const,
        ),
      );
      return Object.fromEntries(entries) as Record<string, boolean>;
    },
  });
  const [dragging, setDragging] = useState<Resume | null>(null);
  const buildFromResume = useMutation({
    mutationFn: (resume: Resume) => api.buildLatexTemplateFromResume(resume),
    onSuccess: (built, resume) => {
      if (!built) {
        toast.error(
          `${resume.name} has no original document stored, so there is no design to read.`,
        );
        return;
      }
      toast.success(`Built the ${built.template.name} template from ${resume.name}`, {
        description: buildDescription(built),
      });
      qc.invalidateQueries({ queryKey: ["templates"] });
    },
    onError: (error: Error) => reportFailure("read that resume's design", error),
  });
  const buildFromUpload = useMutation({
    mutationFn: (file: File) => api.buildLatexTemplate(file),
    onSuccess: (built) => {
      toast.success(`Built the ${built.template.name} template`, {
        description: buildDescription(built),
      });
      qc.invalidateQueries({ queryKey: ["templates"] });
    },
    onError: (error: Error) => reportFailure("build a template from that file", error),
  });
  const removeTemplate = useMutation({
    mutationFn: (templateId: string) => api.archiveTemplate(templateId),
    onSuccess: () => {
      toast.success("Template removed. No resume was affected.");
      qc.invalidateQueries({ queryKey: ["templates"] });
    },
    onError: (error: Error) => reportFailure("remove that template", error),
  });
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [baseRole, setBaseRole] = useState("");
  const masterInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [importResult, setImportResult] = useState<ResumeImportItem[]>([]);

  const create = useMutation({
    mutationFn: () =>
      api.createResume({
        name: name.trim(),
        base_role: baseRole.trim() || null,
        is_master: false,
      }),
    onSuccess: () => {
      toast.success("Source resume created");
      qc.invalidateQueries({ queryKey: ["resumes"] });
      setCreating(false);
      setName("");
      setBaseRole("");
    },
    onError: (err: Error) => reportFailure("create that source resume", err),
  });

  const importFiles = useMutation({
    mutationFn: async ({
      files,
      masterFilename,
    }: {
      files: File[];
      masterFilename?: string;
    }) => {
      const ordered = [...files].sort((left, right) => {
        if (left.name === masterFilename) return -1;
        if (right.name === masterFilename) return 1;
        return left.name.localeCompare(right.name);
      });
      const items: ResumeImportItem[] = [];

      // Resume extraction calls an AI parser. Small sequential requests avoid
      // proxy timeouts while preserving every completed import if one file fails.
      for (const file of ordered) {
        try {
          const result = await api.importResumes(
            [file],
            "Resume library",
            file.name === masterFilename ? masterFilename : undefined,
          );
          items.push(...result.items);
        } catch (error) {
          items.push({
            filename: file.name,
            imported: false,
            resume_id: null,
            version_id: null,
            is_master: file.name === masterFilename,
            note: error instanceof Error ? error.message : "Import failed",
          });
        }
      }

      return { items };
    },
    onSuccess: ({ items }) => {
      const imported = items.filter((item) => item.imported).length;
      const failed = items.length - imported;
      toast.success(`${imported} resume${imported === 1 ? "" : "s"} imported`, {
        description: failed ? `${failed} file${failed === 1 ? "" : "s"} need attention.` : undefined,
      });
      setImportResult(items);
      qc.invalidateQueries({ queryKey: ["resumes"] });
    },
    onError: (error: Error) => reportFailure("import those resumes", error),
  });

  return (
    <div className="workspace-page max-w-6xl">
      <PageIntro
        eyebrow="Document library"
        title="Resume studio"
        description="Source resumes hold your data, templates hold the look. Tailoring combines the two and saves the result under a source, leaving the template untouched."
        icon={LibraryBig}
        action={
          <div className="flex flex-wrap gap-2">
            <input
              ref={masterInput}
              type="file"
              accept=".pdf,.docx,.json,application/pdf,application/json"
              className="hidden"
              onChange={(event) => {
                const files = Array.from(event.target.files ?? []);
                if (files.length) {
                  importFiles.mutate({
                    files,
                    masterFilename: files[0]?.name,
                  });
                }
                event.target.value = "";
              }}
            />
            <input
              ref={folderInput}
              type="file"
              multiple
              accept=".pdf,.docx,.json,application/pdf,application/json"
              className="hidden"
              {...({
                webkitdirectory: "",
                directory: "",
              } as React.InputHTMLAttributes<HTMLInputElement>)}
              onChange={(event) => {
                const files = Array.from(event.target.files ?? []).filter(
                  (file) =>
                    /^Hemnaath_Balasubramani_/i.test(file.name) &&
                    /\.(pdf|docx|json)$/i.test(file.name),
                );
                if (files.length) {
                  importFiles.mutate({
                    files,
                    masterFilename: files.find((file) =>
                      /master/i.test(file.name),
                    )?.name,
                  });
                }
                event.target.value = "";
              }}
            />
            <button
              onClick={() => masterInput.current?.click()}
              disabled={importFiles.isPending}
              className="kinetic-button kinetic-button-secondary disabled:opacity-50"
            >
              <FileUp className="size-3.5" />
              {importFiles.isPending ? "Importing…" : "Set master"}
            </button>
            <button
              onClick={() => folderInput.current?.click()}
              disabled={importFiles.isPending}
              className="kinetic-button kinetic-button-secondary disabled:opacity-50"
            >
              <FolderOpen className="size-3.5" />
              Import folder
            </button>
            <button
              onClick={() => setCreating((current) => !current)}
              className="kinetic-button kinetic-button-primary"
            >
              <Plus className="size-3.5" /> New source
            </button>
          </div>
        }
      >
        <InfoChip tone="sage">{resumes.length} resumes</InfoChip>
        <InfoChip>Evidence-backed bullets</InfoChip>
        <InfoChip tone="clay">AI quality gate</InfoChip>
      </PageIntro>

      <p className="mt-3 max-w-prose text-xs leading-5 text-[color:var(--color-text-dim)]">
        Upload one canonical master or import an existing resume library. A
        file named Master becomes the protected source, originals stay on your
        device, and each imported copy becomes a recoverable revision.
      </p>

      {importResult.length > 0 && (
        <div className="workspace-panel mt-5 p-4">
          <div className="text-sm font-semibold">Latest import</div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {importResult.map((item) => (
              <div
                key={item.filename}
                className="flex items-start gap-2 rounded-lg bg-[color:var(--color-surface-2)] px-3 py-2 text-xs"
              >
                {item.imported ? (
                  <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-[color:var(--color-mint)]" />
                ) : (
                  <Archive className="mt-0.5 size-3.5 shrink-0 text-[color:var(--color-amber)]" />
                )}
                <div className="min-w-0">
                  <div className="truncate font-medium text-[color:var(--color-text-muted)]">
                    {item.filename}
                  </div>
                  <div className="mt-0.5 text-[color:var(--color-text-dim)]">
                    {item.is_master ? "Protected master. " : ""}
                    {item.note}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {creating && (
        <div className="workspace-panel mt-5 p-5">
          <div className="flex flex-col gap-2 sm:flex-row">
            <label htmlFor="new-source-name" className="sr-only">
              Source name
            </label>
            <input
              id="new-source-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name (e.g. SWE, ML, AI)"
              autoFocus
              className="field-control flex-1"
            />
            <label htmlFor="new-source-role" className="sr-only">
              Base role (optional)
            </label>
            <input
              id="new-source-role"
              type="text"
              value={baseRole}
              onChange={(e) => setBaseRole(e.target.value)}
              placeholder="Base role (optional)"
              className="field-control sm:w-48"
            />
            <button
              onClick={() => create.mutate()}
              disabled={create.isPending || !name.trim()}
              className="kinetic-button kinetic-button-primary disabled:opacity-50"
            >
              {create.isPending ? "Creating…" : "Create"}
            </button>
            <button
              onClick={() => setCreating(false)}
              className="kinetic-button kinetic-button-secondary"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="loading-surface mt-6" />
      )}

      {!isLoading && resumes.length === 0 && (
        <EmptyState
          icon={FileText}
          title="No resumes yet"
          description="Choose Set master to upload the canonical PDF, DOCX, or JSON Resume."
        />
      )}

      {!isLoading && resumes.length > 0 && (
        <>
          <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
            <div className="text-xs text-[color:var(--color-text-dim)]">
              {resumes.length} resume{resumes.length === 1 ? "" : "s"}
              {", "}
              {templates.length} template{templates.length === 1 ? "" : "s"}
            </div>
            <label className="flex items-center gap-2 text-xs text-[color:var(--color-text-muted)]">
              <ArrowDownWideNarrow className="size-3.5" />
              <span>Sort</span>
              <Select
                value={sort}
                onChange={(value) => setSort(value as ResumeSort)}
                aria-label="Sort resumes"
                className="w-40"
                options={RESUME_SORT_OPTIONS}
              />
            </label>
          </div>

          <TemplatesSection
            templates={templates}
            onRemove={(id) => removeTemplate.mutate(id)}
            removingId={
              removeTemplate.isPending ? (removeTemplate.variables ?? null) : null
            }
            onUpload={(file) => buildFromUpload.mutate(file)}
            uploading={buildFromUpload.isPending}
            accepts={!!dragging && (originals[dragging.id] ?? false)}
            onDropResume={() => dragging && buildFromResume.mutate(dragging)}
            building={buildFromResume.isPending}
          />

          <SourceResumesSection
            resumes={sorted}
            openId={openId}
            originals={originals}
            onDragStart={setDragging}
            onDragEnd={() => setDragging(null)}
            onUseAsTemplate={(resume) => buildFromResume.mutate(resume)}
            buildingId={
              buildFromResume.isPending
                ? (buildFromResume.variables?.id ?? null)
                : null
            }
          />
        </>
      )}
    </div>
  );
}

/** What actually happened while building a template, said plainly. */
function buildDescription(built: { attempts: number; repairs: string[] }): string {
  if (built.attempts <= 1) return "Compiled on the first pass.";
  return `Took ${built.attempts} passes: the first LaTeX did not compile and was repaired.`;
}

/**
 * The looks available to render with: the six that ship with the app, plus any
 * built from the user's own uploads. A template holds LaTeX only, never resume
 * data.
 *
 * There is no selection here. This page is the library, and choosing a look
 * belongs to the run that uses it, on the tailor page. What this offers is
 * looking: every card is the real sample render, and Full size opens the PDF the
 * renderer produced.
 */
function TemplatesSection({
  templates,
  onRemove,
  removingId,
  onUpload,
  uploading,
  accepts,
  onDropResume,
  building,
}: {
  templates: ResumeTemplate[];
  onRemove: (templateId: string) => void;
  removingId: string | null;
  onUpload: (file: File) => void;
  uploading: boolean;
  accepts: boolean;
  onDropResume: () => void;
  building: boolean;
}) {
  const [over, setOver] = useState(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const active = accepts && over;
  const busy = uploading || building;
  const mine = templates.filter((template) => template.kind === "custom").length;

  return (
    <section
      className="mt-6"
      onDragOver={(event) => {
        if (!accepts) return;
        // Marking the event handled is what makes this a valid drop target.
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={(event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node)) return;
        setOver(false);
      }}
      onDrop={(event) => {
        if (!accepts) return;
        event.preventDefault();
        setOver(false);
        onDropResume();
      }}
    >
      <div className="flex flex-wrap items-baseline gap-2">
        <LayoutTemplate className="size-4 shrink-0 text-[color:var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold">Templates</h2>
        <span className="text-xs text-[color:var(--color-text-dim)]">
          {templates.length}
          {mine > 0 ? `, ${mine} yours` : ""}
        </span>
        {active && (
          <span className="text-xs text-[color:var(--color-violet)]">
            Drop to rebuild this resume&apos;s design as a template
          </span>
        )}
        {busy && (
          <span className="inline-flex items-center gap-1.5 text-xs text-[color:var(--color-text-dim)]">
            <Loader2 className="size-3 animate-spin" />
            Writing the LaTeX and compiling it, up to a few minutes
          </span>
        )}
        <input
          ref={uploadInput}
          type="file"
          accept=".tex,.pdf,application/pdf,text/x-tex"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onUpload(file);
            event.target.value = "";
          }}
        />
        <button
          onClick={() => uploadInput.current?.click()}
          disabled={busy}
          className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1 text-[11px] text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)] disabled:opacity-50"
        >
          <FileUp className="size-3" /> Add your own
        </button>
      </div>
      <p className="mt-1 pl-6 text-xs leading-5 text-[color:var(--color-text-dim)]">
        The look, not the data. Every preview is a real render of invented sample
        data. Add your own by uploading a <strong>.tex</strong>, which keeps the
        design exactly, or a <strong>.pdf</strong>, which gets rebuilt as LaTeX
        and comes close rather than matching.
      </p>
      <div
        className={`mt-3 rounded-[var(--radius-card)] transition ${
          active
            ? "ring-2 ring-[color:var(--color-violet)]/60"
            : accepts
              ? "ring-1 ring-dashed ring-[color:var(--color-border)]"
              : ""
        }`}
      >
        <TemplatePicker
          templates={templates}
          selectable={false}
          value=""
          onChange={() => {}}
          onRemove={(templateId) => {
            const template = templates.find((item) => item.id === templateId);
            if (
              window.confirm(
                `Remove the ${template?.name ?? "selected"} template? This deletes the saved look only. No resume, version or PDF is touched.`,
              )
            ) {
              onRemove(templateId);
            }
          }}
          removingId={removingId}
        />
      </div>
    </section>
  );
}

/** Every resume. Tailored versions are saved under the one they came from. */
function SourceResumesSection({
  resumes,
  openId,
  originals,
  onDragStart,
  onDragEnd,
  onUseAsTemplate,
  buildingId,
}: {
  resumes: Resume[];
  openId: string | null;
  originals: Record<string, boolean>;
  onDragStart: (resume: Resume) => void;
  onDragEnd: () => void;
  onUseAsTemplate: (resume: Resume) => void;
  buildingId: string | null;
}) {
  return (
    <section className="mt-6">
      <div className="flex items-baseline gap-2">
        <FolderOpen className="size-4 shrink-0 text-[color:var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold">Source resumes</h2>
        <span className="text-xs text-[color:var(--color-text-dim)]">
          {resumes.length}
        </span>
      </div>
      <p className="mt-1 pl-6 text-xs leading-5 text-[color:var(--color-text-dim)]">
        The data. Tailored versions are saved here, under the resume they came
        from. Drag one up to Templates to reuse its design.
      </p>
      {resumes.length === 0 ? (
        <div className="workspace-panel mt-3 px-5 py-4 text-xs text-[color:var(--color-text-dim)]">
          No resumes yet.
        </div>
      ) : (
        <div className="workspace-panel mt-3 divide-y divide-[color:var(--color-border)] overflow-hidden">
          {resumes.map((r) => (
            <ResumeRow
              key={r.id}
              resume={r}
              defaultOpen={r.id === openId}
              hasOriginal={originals[r.id] ?? false}
              onDragStart={onDragStart}
              onDragEnd={onDragEnd}
              onUseAsTemplate={onUseAsTemplate}
              building={buildingId === r.id}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function ResumeRow({
  resume,
  defaultOpen = false,
  hasOriginal,
  onDragStart,
  onDragEnd,
  onUseAsTemplate,
  building,
}: {
  resume: Resume;
  defaultOpen?: boolean;
  hasOriginal: boolean;
  onDragStart: (resume: Resume) => void;
  onDragEnd: () => void;
  onUseAsTemplate: (resume: Resume) => void;
  building: boolean;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(defaultOpen);
  // Versions load on expand, not on mount. A library of 30+ resumes would
  // otherwise fire one request per row the moment the page opens.
  const { data: versions = [], isLoading } = useQuery({
    queryKey: ["versions", resume.id],
    queryFn: () => api.listVersions(resume.id),
    enabled: open,
  });
  const removeResume = useMutation({
    mutationFn: () => api.deleteResume(resume.id),
    onSuccess: () => {
      toast.success("Resume archived", {
        description: "Its versions remain stored in the database.",
      });
      qc.invalidateQueries({ queryKey: ["resumes"] });
    },
    onError: (error: Error) => reportFailure("archive that resume", error),
  });

  const updated = format(
    new Date(resume.updated_at || resume.created_at),
    "MMM d, yyyy",
  );

  return (
    <div>
      <div
        // Only a resume with a stored original document has a design to read, so
        // the ones without are not draggable rather than dragging and failing.
        draggable={hasOriginal}
        onDragStart={(event) => {
          if (!hasOriginal) return;
          event.dataTransfer.setData("text/plain", resume.id);
          event.dataTransfer.effectAllowed = "copy";
          onDragStart(resume);
        }}
        onDragEnd={onDragEnd}
        className={`flex items-center gap-3 px-5 py-3 transition hover:bg-[color:var(--color-surface-2)] ${
          hasOriginal ? "cursor-grab active:cursor-grabbing" : ""
        } ${building ? "opacity-50" : ""}`}
      >
        <button
          onClick={() => setOpen((current) => !current)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
        >
          <ChevronRight
            className={`size-3.5 shrink-0 text-[color:var(--color-text-dim)] transition-transform ${
              open ? "rotate-90" : ""
            }`}
          />
          {resume.is_master ? (
            <Crown className="size-4 shrink-0 text-[color:var(--color-amber)]" />
          ) : (
            <Sparkles className="size-4 shrink-0 text-[color:var(--color-violet)]" />
          )}
          <span className="min-w-0 truncate text-sm font-semibold">
            {resume.name}
          </span>
          {resume.is_master && (
            <span className="shrink-0 rounded-full bg-[color:var(--color-amber)]/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-amber-ink)]">
              master
            </span>
          )}
          {resume.base_role && (
            <span className="hidden min-w-0 truncate text-xs text-[color:var(--color-text-muted)] sm:inline">
              {resume.base_role}
            </span>
          )}
        </button>
        <div className="shrink-0 text-xs tabular-nums text-[color:var(--color-text-dim)]">
          {updated}
        </div>
        {/* Keyboard and touch equivalent of the drag. Says why when there is no
            original document, rather than offering something that cannot work. */}
        {hasOriginal ? (
          <button
            onClick={() => onUseAsTemplate(resume)}
            disabled={building}
            title="Read this resume's design and save it as a template"
            className="shrink-0 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2.5 py-1 text-[11px] text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)] disabled:opacity-50"
          >
            {building ? "Reading…" : "Use as template"}
          </button>
        ) : (
          <span
            title="This resume has no original document stored, so there is no design to read."
            className="shrink-0 text-[11px] text-[color:var(--color-text-dim)]"
          >
            No original
          </span>
        )}
        {!resume.is_master && (
          <button
            onClick={() => {
              if (window.confirm(`Archive ${resume.name}? Its versions will remain stored.`)) {
                removeResume.mutate();
              }
            }}
            className="shrink-0 rounded-lg p-1.5 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-rose)]/10 hover:text-[color:var(--color-rose-ink)]"
            aria-label={`Archive ${resume.name}`}
          >
            <Archive className="size-3.5" />
          </button>
        )}
      </div>

      {open && (
        <div className="divide-y divide-[color:var(--color-border)] border-t border-[color:var(--color-border)] bg-[color:var(--color-surface-2)]/40">
          {isLoading && (
            <div className="px-5 py-3 pl-12 text-sm text-[color:var(--color-text-muted)]">
              Loading versions…
            </div>
          )}
          {!isLoading && versions.length === 0 && (
            <div className="px-5 py-3 pl-12 text-sm text-[color:var(--color-text-muted)]">
              No versions yet. Tailoring this resume for a job saves the first one.
            </div>
          )}
          {versions.map((v) => (
            <VersionRow key={v.id} version={v} resumeId={resume.id} />
          ))}
        </div>
      )}
    </div>
  );
}

function VersionRow({
  version,
  resumeId,
}: {
  version: ResumeVersionSummary;
  resumeId: string;
}) {
  const qc = useQueryClient();
  const downloadUrl = api.downloadVersionUrl(resumeId, version.id);
  const created = format(new Date(version.created_at), "MMM d, yyyy");
  const removeVersion = useMutation({
    mutationFn: () => api.deleteVersion(resumeId, version.id),
    onSuccess: () => {
      toast.success("Version archived", {
        description: "The revision remains stored in the database.",
      });
      qc.invalidateQueries({ queryKey: ["versions", resumeId] });
    },
    onError: (error: Error) => reportFailure("archive that version", error),
  });
  return (
    <div className="flex items-center justify-between py-3 pl-12 pr-5 hover:bg-[color:var(--color-surface-2)]">
      <div className="flex items-center gap-3">
        <FileText className="size-4 text-[color:var(--color-text-muted)]" />
        <div>
          <div className="text-sm font-medium">{created}</div>
          <div className="flex items-center gap-2 text-xs text-[color:var(--color-text-dim)]">
            {version.status === "final" ? (
              <span className="inline-flex items-center gap-1 text-[color:var(--color-mint-ink)]">
                <CheckCircle2 className="size-3" /> Final
              </span>
            ) : (
              <span>{versionStatusLabel(version.status)}</span>
            )}
            {version.ats_score !== null && version.ats_score !== undefined && (
              <>
                <span>·</span>
                <span>ATS {version.ats_score}</span>
              </>
            )}
            {version.review_score !== null && version.review_score !== undefined && (
              <>
                <span>·</span>
                <span>QA {Math.round(Number(version.review_score))}</span>
              </>
            )}
            {version.spawned_from_job_id && (
              <>
                <span>·</span>
                <span>Tailored</span>
              </>
            )}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Link
          href={`/resumes/${resumeId}/${version.id}`}
          className="kinetic-button kinetic-button-secondary min-h-0 px-3 py-1.5"
        >
          <MessageSquareText className="size-3" />
          Open
        </Link>
        {version.status === "final" && (
          <button
            onClick={() =>
              downloadPdf(downloadUrl, `resume_${version.id.slice(0, 8)}.pdf`)
            }
            className="kinetic-button kinetic-button-primary min-h-0 px-3 py-1.5"
          >
            <Download className="size-3" />
            Download PDF
          </button>
        )}
        <button
              onClick={() => {
                if (window.confirm("Archive this resume version? It remains stored in the database.")) removeVersion.mutate();
              }}
          className="rounded-lg p-2 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-rose)]/10 hover:text-[color:var(--color-rose-ink)]"
          aria-label="Archive resume version"
        >
          <Archive className="size-3.5" />
        </button>
      </div>
    </div>
  );
}
