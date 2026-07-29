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
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";
import { downloadPdf } from "@/lib/download";
import type {
  Resume,
  ResumeCategory,
  ResumeImportItem,
  ResumeVersionSummary,
} from "@/lib/types";

/** Which half of the library a resume sits in. Master is always a source. */
function resumeCategory(resume: Resume): ResumeCategory {
  return resume.category === "template" && !resume.is_master
    ? "template"
    : "source";
}

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
  // Template carries the look, source carries the data. Rows written before the
  // split have no category and read as source, so nothing needs backfilling.
  const templates = useMemo(
    () => sorted.filter((r) => r.category === "template" && !r.is_master),
    [sorted],
  );
  const sources = useMemo(
    () => sorted.filter((r) => r.category !== "template" || r.is_master),
    [sorted],
  );
  // Dragging state lives here because a drop target has to know what is being
  // dragged, and dragover events cannot read dataTransfer for security reasons.
  const [dragging, setDragging] = useState<Resume | null>(null);
  const move = useMutation({
    mutationFn: ({
      resume,
      category,
    }: {
      resume: Resume;
      category: ResumeCategory;
    }) => api.setResumeCategory(resume.id, category),
    onSuccess: (updated) => {
      toast.success(
        updated.category === "template"
          ? `${updated.name} is now a template`
          : `${updated.name} moved to source resumes`,
      );
      qc.invalidateQueries({ queryKey: ["resumes"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });
  /** Can this resume be dropped into that section? */
  const canDrop = (resume: Resume | null, category: ResumeCategory): boolean => {
    if (!resume) return false;
    // Master holds the verified data tailoring starts from, so it stays a source.
    if (resume.is_master) return false;
    return resumeCategory(resume) !== category;
  };
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
    onError: (err: Error) => toast.error(err.message),
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
    onError: (error: Error) => toast.error(error.message),
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

      <p className="mt-3 text-xs leading-5 text-[color:var(--color-text-dim)]">
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
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name (e.g. SWE, ML, AI)"
              autoFocus
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
              {sources.length} source{sources.length === 1 ? "" : "s"}
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

          <LibrarySection
            title="Templates"
            description="The look. Drag a resume here to use its design. Tailoring never writes to a template, it only borrows the look."
            icon={LayoutTemplate}
            category="template"
            resumes={templates}
            openId={openId}
            emptyHint="No templates yet. Drag a source resume up here, or use its Use as template button."
            accepts={canDrop(dragging, "template")}
            onDropResume={() =>
              dragging && move.mutate({ resume: dragging, category: "template" })
            }
            onDragStart={setDragging}
            onDragEnd={() => setDragging(null)}
            onMove={(resume) => move.mutate({ resume, category: "source" })}
            movingId={move.isPending ? (move.variables?.resume.id ?? null) : null}
          />

          <LibrarySection
            title="Source resumes"
            description="The data. Tailored versions are saved here, under the resume they came from."
            icon={FolderOpen}
            category="source"
            resumes={sources}
            openId={openId}
            emptyHint="No source resumes yet."
            accepts={canDrop(dragging, "source")}
            onDropResume={() =>
              dragging && move.mutate({ resume: dragging, category: "source" })
            }
            onDragStart={setDragging}
            onDragEnd={() => setDragging(null)}
            onMove={(resume) => move.mutate({ resume, category: "template" })}
            movingId={move.isPending ? (move.variables?.resume.id ?? null) : null}
          />
        </>
      )}
    </div>
  );
}

function LibrarySection({
  title,
  description,
  icon: Icon,
  category,
  resumes,
  openId,
  emptyHint,
  accepts,
  onDropResume,
  onDragStart,
  onDragEnd,
  onMove,
  movingId,
}: {
  title: string;
  description: string;
  icon: typeof FolderOpen;
  category: ResumeCategory;
  resumes: Resume[];
  openId: string | null;
  emptyHint: string;
  accepts: boolean;
  onDropResume: () => void;
  onDragStart: (resume: Resume) => void;
  onDragEnd: () => void;
  onMove: (resume: Resume) => void;
  movingId: string | null;
}) {
  const [over, setOver] = useState(false);
  const active = accepts && over;

  return (
    <section
      className="mt-6"
      // Dropping is how the user asked to do this. preventDefault on dragover
      // is what makes an element a valid drop target at all.
      onDragOver={(event) => {
        if (!accepts) return;
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={(event) => {
        // Ignore the events fired while moving between this section's children.
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
      <div className="flex items-baseline gap-2">
        <Icon className="size-4 shrink-0 text-[color:var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold">{title}</h2>
        <span className="text-xs text-[color:var(--color-text-dim)]">
          {resumes.length}
        </span>
        {active && (
          <span className="text-xs text-[color:var(--color-violet)]">
            Drop to move here
          </span>
        )}
      </div>
      <p className="mt-1 pl-6 text-xs leading-5 text-[color:var(--color-text-dim)]">
        {description}
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
        {resumes.length === 0 ? (
          <div className="workspace-panel px-5 py-4 text-xs text-[color:var(--color-text-dim)]">
            {accepts ? "Drop a resume here, or " : ""}
            {accepts ? emptyHint[0].toLowerCase() + emptyHint.slice(1) : emptyHint}
          </div>
        ) : (
          <div className="workspace-panel divide-y divide-[color:var(--color-border)] overflow-hidden">
            {resumes.map((r) => (
              <ResumeRow
                key={r.id}
                resume={r}
                defaultOpen={r.id === openId}
                category={category}
                onDragStart={onDragStart}
                onDragEnd={onDragEnd}
                onMove={onMove}
                moving={movingId === r.id}
              />
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function ResumeRow({
  resume,
  defaultOpen = false,
  category,
  onDragStart,
  onDragEnd,
  onMove,
  moving,
}: {
  resume: Resume;
  defaultOpen?: boolean;
  category: ResumeCategory;
  onDragStart: (resume: Resume) => void;
  onDragEnd: () => void;
  onMove: (resume: Resume) => void;
  moving: boolean;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(defaultOpen);
  const isTemplate = category === "template";
  const draggable = !resume.is_master;
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
    onError: (error: Error) => toast.error(error.message),
  });

  const updated = format(
    new Date(resume.updated_at || resume.created_at),
    "MMM d, yyyy",
  );

  return (
    <div>
      <div
        draggable={draggable}
        onDragStart={(event) => {
          if (!draggable) return;
          // Some browsers refuse the drag without payload, even unused.
          event.dataTransfer.setData("text/plain", resume.id);
          event.dataTransfer.effectAllowed = "move";
          onDragStart(resume);
        }}
        onDragEnd={onDragEnd}
        className={`flex items-center gap-3 px-5 py-3 transition hover:bg-[color:var(--color-surface-2)] ${
          draggable ? "cursor-grab active:cursor-grabbing" : ""
        } ${moving ? "opacity-50" : ""}`}
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
            <span className="shrink-0 rounded-full bg-[color:var(--color-amber)]/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-amber)]">
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
        {/* Keyboard and touch equivalent of the drag gesture, which native
            HTML5 drag-and-drop cannot offer. Master holds the verified data
            tailoring starts from, so it can never become a template. */}
        {!resume.is_master && (
          <button
            onClick={() => onMove(resume)}
            disabled={moving}
            title={
              isTemplate
                ? "Move back to source resumes"
                : "Use this resume's look as a template"
            }
            className="shrink-0 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2.5 py-1 text-[11px] text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)] disabled:opacity-50"
          >
            {moving
              ? "Moving…"
              : isTemplate
                ? "Move to source"
                : "Use as template"}
          </button>
        )}
        {!resume.is_master && (
          <button
            onClick={() => {
              if (window.confirm(`Archive ${resume.name}? Its versions will remain stored.`)) {
                removeResume.mutate();
              }
            }}
            className="shrink-0 rounded-lg p-1.5 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-rose)]/10 hover:text-[color:var(--color-rose)]"
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
              loading versions…
            </div>
          )}
          {!isLoading && versions.length === 0 && (
            <div className="px-5 py-3 pl-12 text-sm text-[color:var(--color-text-muted)]">
              no versions yet
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
    onError: (error: Error) => toast.error(error.message),
  });
  return (
    <div className="flex items-center justify-between py-3 pl-12 pr-5 hover:bg-[color:var(--color-surface-2)]">
      <div className="flex items-center gap-3">
        <FileText className="size-4 text-[color:var(--color-text-muted)]" />
        <div>
          <div className="text-sm font-medium">{created}</div>
          <div className="flex items-center gap-2 text-xs text-[color:var(--color-text-dim)]">
            {version.status === "final" ? (
              <span className="inline-flex items-center gap-1 text-[color:var(--color-mint)]">
                <CheckCircle2 className="size-3" /> final
              </span>
            ) : (
              <span>{version.status.replaceAll("_", " ")}</span>
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
                <span>tailored</span>
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
          className="rounded-lg p-2 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-rose)]/10 hover:text-[color:var(--color-rose)]"
          aria-label="Archive resume version"
        >
          <Archive className="size-3.5" />
        </button>
      </div>
    </div>
  );
}
