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
import { ProjectFolder } from "@/components/project-folder";
import { ResumeVersionPreview } from "@/components/resume-version-preview";
import { TemplatePicker } from "@/components/template-picker";
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";
import { reportFailure } from "@/lib/errors";
import { appwriteWorkspace } from "@/lib/appwrite/workspace";
import { downloadPdf } from "@/lib/download";
import { versionStatusLabel } from "@/lib/types";
import type {
  Application,
  Resume,
  ResumeImportItem,
  ResumeTemplate,
  ResumeVersionSummary,
} from "@/lib/types";

/** What a version's "for {company}" chip needs — just enough to identify the
 * pipeline entry a version was tailored for, not the whole Application. */
interface ApplicationRef {
  company: string | null;
  companyDomain: string | null;
  jobTitle: string;
}

/** Most recent activity on a resume, for sorting. Falls back to creation. */
function resumeTimestamp(resume: Resume): number {
  const parsed = Date.parse(resume.updated_at || resume.created_at);
  return Number.isFinite(parsed) ? parsed : 0;
}

type ResumeSort = "priority" | "newest" | "oldest" | "name";

const RESUME_SORT_OPTIONS: { value: ResumeSort; label: string }[] = [
  { value: "priority", label: "Most tailored first" },
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
  // Only for labeling versions with the company/role they were tailored for
  // (spawned_from_application_id) — this page never mutates an application.
  const { data: applications = [] } = useQuery({
    queryKey: ["applications"],
    queryFn: () => api.listApplications(),
  });
  const applicationById = useMemo(() => {
    const map = new Map<string, ApplicationRef>();
    for (const a of applications as Application[]) {
      map.set(a.id, {
        company: a.job.company?.name ?? null,
        companyDomain: a.job.company?.domain ?? null,
        jobTitle: a.job.title,
      });
    }
    return map;
  }, [applications]);
  const [sort, setSort] = useState<ResumeSort>("priority");
  // The master is pinned as its own preview above these.
  const master = resumes.find((r) => r.is_master);
  // Company-tailored resumes (spawned_from_application_id set) are a
  // different kind of thing from a general-purpose data identity like the
  // master or "AI / Backend SWE" — one is a specific output for one
  // application, the other is a base every tailored version traces back to.
  // Mixing them into one "Source resumes" list is how a handful of real
  // sources ends up buried under dozens of one-off company resumes.
  const companyResumes = useMemo(
    () =>
      resumes
        .filter((r) => !r.is_master && !!r.spawned_from_application_id)
        .sort((left, right) => resumeTimestamp(right) - resumeTimestamp(left)),
    [resumes],
  );
  const sorted = useMemo(() => {
    const rows = resumes.filter((r) => !r.is_master && !r.spawned_from_application_id);
    if (sort === "name") {
      return rows.sort((left, right) => left.name.localeCompare(right.name));
    }
    if (sort === "priority") {
      return rows.sort((left, right) => {
        if (right.tailored_count !== left.tailored_count) {
          return right.tailored_count - left.tailored_count;
        }
        return resumeTimestamp(right) - resumeTimestamp(left);
      });
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

          {master && (
            <MasterResumeCard
              resume={master}
              hasOriginal={originals[master.id] ?? false}
              onUseAsTemplate={(resume) => buildFromResume.mutate(resume)}
              building={buildFromResume.isPending && buildFromResume.variables?.id === master.id}
            />
          )}

          <CompanyResumesGrid
            resumes={companyResumes}
            applicationById={applicationById}
            openId={openId}
          />

          <TemplatesSection
            templates={templates}
            onRemove={(id) => removeTemplate.mutate(id)}
            removingId={
              removeTemplate.isPending ? (removeTemplate.variables ?? null) : null
            }
            onUpload={(file) => buildFromUpload.mutate(file)}
            uploading={buildFromUpload.isPending}
            building={buildFromResume.isPending}
          />

          <SourceResumesGrid
            resumes={sorted}
            openId={openId}
            originals={originals}
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
 * The master, pinned above everything else rather than buried in the Source
 * resumes list at whatever position its sort key lands it — it's the one
 * resume every tailored version ultimately traces back to, so it reads as a
 * preview of "the canonical you," not just another row. Its own most recent
 * render is the thumbnail: there's no company logo to stand in for it, and
 * nothing says "this is you" better than the actual document.
 */
function MasterResumeCard({
  resume,
  hasOriginal,
  onUseAsTemplate,
  building,
}: {
  resume: Resume;
  hasOriginal: boolean;
  onUseAsTemplate: (resume: Resume) => void;
  building: boolean;
}) {
  const [showHistory, setShowHistory] = useState(false);
  const { data: versions = [] } = useQuery({
    queryKey: ["versions", resume.id],
    queryFn: () => api.listVersions(resume.id),
  });
  const [latest, ...earlier] = versions;
  const updated = format(new Date(resume.updated_at || resume.created_at), "MMM d, yyyy");

  return (
    <section className="mt-6">
      <div className="flex items-center gap-2">
        <Crown className="size-4 shrink-0 text-[color:var(--color-amber)]" />
        <h2 className="text-sm font-semibold">Master resume</h2>
      </div>
      <div className="workspace-panel mt-3 overflow-hidden ring-1 ring-[color:var(--color-amber)]/25">
        <ResumeCardBody
          resume={resume}
          version={latest}
          thumbnail={
            <ResumeVersionPreview
              downloadUrl={latest ? api.downloadVersionUrl(resume.id, latest.id) : null}
              label={`${resume.name}, the master resume`}
            />
          }
          badge="master"
          subtitle={updated}
          hasOriginal={hasOriginal}
          onUseAsTemplate={() => onUseAsTemplate(resume)}
          building={building}
        />
        {earlier.length > 0 && (
          <div className="border-t border-[color:var(--color-border)]">
            <button
              onClick={() => setShowHistory((current) => !current)}
              aria-expanded={showHistory}
              className="flex w-full items-center gap-2 px-5 py-2.5 text-left text-xs text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-2)]"
            >
              <ChevronRight
                className={`size-3 shrink-0 transition-transform ${showHistory ? "rotate-90" : ""}`}
              />
              {earlier.length} earlier version{earlier.length === 1 ? "" : "s"}
            </button>
            {showHistory && (
              <div className="divide-y divide-[color:var(--color-border)] border-t border-[color:var(--color-border)] bg-[color:var(--color-surface-2)]/40">
                {earlier.map((version) => (
                  <VersionRow
                    key={version.id}
                    version={version}
                    resumeId={resume.id}
                    resumeName={resume.name}
                    applicationRef={undefined}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

/**
 * One resume's primary card body: thumbnail, name, and the two actions that
 * matter — open the thing, or get the file — sized as real buttons rather
 * than icons alone, so there's something bigger than a few pixels to aim at.
 * Shared by the master and by every source resume, which differ only in
 * their thumbnail and their badge.
 */
function ResumeCardBody({
  resume,
  version,
  thumbnail,
  badge,
  subtitle,
  hasOriginal,
  onUseAsTemplate,
  building,
}: {
  resume: Resume;
  version: ResumeVersionSummary | undefined;
  thumbnail: React.ReactNode;
  badge?: string;
  subtitle: string;
  hasOriginal: boolean;
  onUseAsTemplate: () => void;
  building: boolean;
}) {
  const isUploadedFile = !!version?.source_filename;
  const downloadUrl = version ? api.downloadVersionUrl(resume.id, version.id) : "";
  const downloadName =
    version?.source_filename ?? fallbackDownloadName(resume.name, version?.created_at ?? resume.updated_at);

  return (
    <div className="flex items-center gap-4 px-5 py-4">
      <span className="aspect-[8.5/11] h-24 w-[4.6rem] shrink-0 overflow-hidden rounded-lg border border-[color:var(--color-border)] bg-white">
        {thumbnail}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="min-w-0 truncate text-sm font-semibold">{resume.name}</span>
          {badge && (
            <span className="shrink-0 rounded-full bg-[color:var(--color-amber)]/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-amber-ink)]">
              {badge}
            </span>
          )}
        </div>
        {resume.base_role && (
          <p className="mt-0.5 truncate text-xs text-[color:var(--color-text-muted)]">
            {resume.base_role}
          </p>
        )}
        <p className="mt-0.5 text-xs text-[color:var(--color-text-dim)]">{subtitle}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {version && !isUploadedFile && (
          <Link
            href={`/resumes/${resume.id}/${version.id}`}
            className="kinetic-button kinetic-button-secondary min-h-0 px-3 py-1.5"
          >
            <MessageSquareText className="size-3" />
            Open
          </Link>
        )}
        {version?.status === "final" && (
          <button
            type="button"
            onClick={() => downloadPdf(downloadUrl, downloadName)}
            className="kinetic-button kinetic-button-primary min-h-0 px-3 py-1.5"
          >
            <Download className="size-3" />
            Download
          </button>
        )}
        {hasOriginal ? (
          <button
            onClick={onUseAsTemplate}
            disabled={building}
            title="Read this resume's design and save it as a template"
            className="shrink-0 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2.5 py-1.5 text-[11px] text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)] disabled:opacity-50"
          >
            {building ? "Reading…" : "Use as template"}
          </button>
        ) : null}
      </div>
    </div>
  );
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
  building,
}: {
  templates: ResumeTemplate[];
  onRemove: (templateId: string) => void;
  removingId: string | null;
  onUpload: (file: File) => void;
  uploading: boolean;
  building: boolean;
}) {
  // Closed by default: the look isn't something you need in front of you
  // every visit, only when you're actually choosing or adding one.
  const [open, setOpen] = useState(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const busy = uploading || building;
  const mine = templates.filter((template) => template.kind === "custom").length;

  return (
    <section className="mt-6">
      <div className="flex flex-wrap items-baseline gap-2">
        <button
          onClick={() => setOpen((current) => !current)}
          aria-expanded={open}
          className="flex min-w-0 items-baseline gap-2 text-left"
        >
          <ChevronRight
            className={`size-3.5 shrink-0 self-center text-[color:var(--color-text-dim)] transition-transform ${
              open ? "rotate-90" : ""
            }`}
          />
          <LayoutTemplate className="size-4 shrink-0 self-center text-[color:var(--color-text-muted)]" />
          <h2 className="text-sm font-semibold">Templates</h2>
          <span className="text-xs text-[color:var(--color-text-dim)]">
            {templates.length}
            {mine > 0 ? `, ${mine} yours` : ""}
          </span>
        </button>
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
          onClick={() => {
            setOpen(true);
            uploadInput.current?.click();
          }}
          disabled={busy}
          className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1 text-[11px] text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)] disabled:opacity-50"
        >
          <FileUp className="size-3" /> Add your own
        </button>
      </div>
      {!open && (
        <p className="mt-1 pl-6 text-xs leading-5 text-[color:var(--color-text-dim)]">
          {templates.length} available. Open to browse or add your own.
        </p>
      )}
      {open && (
        <>
      <p className="mt-1 pl-6 text-xs leading-5 text-[color:var(--color-text-dim)]">
        The look, not the data. Every preview is a real render of invented sample
        data. Add your own by uploading a <strong>.tex</strong>, which keeps the
        design exactly, or a <strong>.pdf</strong>, which gets rebuilt as LaTeX
        and comes close rather than matching.
      </p>
      <div className="mt-3">
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
        </>
      )}
    </section>
  );
}

/**
 * A company's logo as a quick visual anchor. Every tailored resume looks
 * about the same on the page itself, so the logo — not the document — is
 * what actually lets you tell "Daice Labs" apart from "Roblox" at a glance.
 * Falls back to initials on a colored badge when there's no domain, or the
 * favicon lookup itself fails.
 */
function CompanyLogo({
  domain,
  name,
  size = 80,
}: {
  domain: string | null;
  name: string;
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  const style = { width: size, height: size };
  if (!domain || failed) {
    const badgeStyle = { ...style, fontSize: size * 0.36 };
    const initials =
      name
        .split(/\s+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((word) => word[0]?.toUpperCase())
        .join("") || "?";
    return (
      <span
        style={badgeStyle}
        className="grid shrink-0 place-items-center rounded-full bg-[color:var(--color-violet)]/15 font-semibold text-[color:var(--color-violet)]"
      >
        {initials}
      </span>
    );
  }
  return (
    <img
      src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=128`}
      alt=""
      width={size}
      height={size}
      style={style}
      className="shrink-0 rounded-full bg-[color:var(--color-surface-2)] object-contain p-2"
      onError={() => setFailed(true)}
    />
  );
}

/**
 * One resume's card inside a folder overlay: thumbnail, name, and up to two
 * real buttons — the whole thumbnail-and-name area opens the most recent
 * version (a large target, not a tiny icon), and download sits beside it as
 * its own properly sized button. Older versions are named, not stacked into
 * a scrolling list the fixed-size overlay cell can't actually fit.
 */
function FolderResumeCard({
  resume,
  latestVersion,
  versionCount,
  thumbnail,
  subtitle,
}: {
  resume: Resume;
  latestVersion: ResumeVersionSummary | undefined;
  versionCount: number;
  thumbnail: React.ReactNode;
  subtitle: string;
}) {
  const isUploadedFile = !!latestVersion?.source_filename;
  const openHref =
    latestVersion && !isUploadedFile ? `/resumes/${resume.id}/${latestVersion.id}` : null;
  const downloadUrl = latestVersion ? api.downloadVersionUrl(resume.id, latestVersion.id) : "";
  const downloadName =
    latestVersion?.source_filename ??
    fallbackDownloadName(resume.name, latestVersion?.created_at ?? resume.updated_at);

  // A column flex with items-center sizes children to their own content, not
  // to the card's width -- so a long role title never had anything to
  // truncate against and just grew past the card's edge. items-stretch (the
  // default) makes the text block the full card width; text-center still
  // centers the text itself inside that now-constrained box.
  const identity = (
    <>
      <div className="flex items-center justify-center">{thumbnail}</div>
      <div className="w-full min-w-0 text-center">
        <p className="truncate text-sm font-semibold">{resume.name}</p>
        <p className="truncate text-[11px] text-[color:var(--color-text-dim)]">{subtitle}</p>
      </div>
    </>
  );

  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-3">
      {openHref ? (
        <Link
          href={openHref}
          className="flex w-full flex-1 flex-col justify-center gap-2 rounded-lg transition hover:bg-[color:var(--color-surface-hover)]"
        >
          {identity}
        </Link>
      ) : (
        <div className="flex w-full flex-1 flex-col justify-center gap-2">
          {identity}
        </div>
      )}
      <div className="flex w-full shrink-0 items-center justify-center gap-2">
        {versionCount > 1 && (
          <span className="text-[10px] text-[color:var(--color-text-dim)]">
            {versionCount} versions
          </span>
        )}
        {latestVersion?.status === "final" && (
          <button
            type="button"
            onClick={() => downloadPdf(downloadUrl, downloadName)}
            aria-label="Download PDF"
            title="Download PDF"
            className="flex size-8 items-center justify-center rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
          >
            <Download className="size-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * One company, shown small while peeking out of the single "Company
 * resumes" folder and large inside its open overlay — same element,
 * `ProjectFolder` morphs it between the two. The logo is what tells one
 * company apart from another at a glance.
 */
function CompanyPreviewCard({
  resume,
  applicationRef,
}: {
  resume: Resume;
  applicationRef: ApplicationRef | undefined;
}) {
  const { data: versions = [] } = useQuery({
    queryKey: ["versions", resume.id],
    queryFn: () => api.listVersions(resume.id),
  });
  return (
    <FolderResumeCard
      resume={resume}
      latestVersion={versions[0]}
      versionCount={versions.length}
      subtitle={resume.base_role ?? "Tailored resume"}
      thumbnail={
        <CompanyLogo domain={applicationRef?.companyDomain ?? null} name={resume.name} size={48} />
      }
    />
  );
}

/** Every company-tailored resume, stacked inside one folder rather than one
 * folder per company — almost every company only ever has a single resume,
 * so a folder each read as dozens of near-identical boxes. Hovering fans out
 * a few company logos; clicking opens all of them at once. */
function CompanyResumesFolder({
  resumes,
  applicationById,
  defaultExpanded,
}: {
  resumes: Resume[];
  applicationById: Map<string, ApplicationRef>;
  defaultExpanded: boolean;
}) {
  return (
    <ProjectFolder
      title="Company resumes"
      description="Tailored for one specific application"
      ariaLabel={`Company resumes — ${resumes.length} compan${resumes.length === 1 ? "y" : "ies"}`}
      itemLabel="company"
      count={resumes.length}
      defaultExpanded={defaultExpanded}
      frontVisual={
        <Sparkles className="size-12 text-[color:var(--color-violet)] opacity-40" />
      }
      previews={resumes.map((resume) => ({
        id: resume.id,
        content: (
          <CompanyPreviewCard
            resume={resume}
            applicationRef={
              resume.spawned_from_application_id
                ? applicationById.get(resume.spawned_from_application_id)
                : undefined
            }
          />
        ),
      }))}
    />
  );
}

function CompanyResumesGrid({
  resumes,
  applicationById,
  openId,
}: {
  resumes: Resume[];
  applicationById: Map<string, ApplicationRef>;
  openId: string | null;
}) {
  const [open, setOpen] = useState(true);
  return (
    <section className="mt-6">
      <button
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex items-baseline gap-2 text-left"
      >
        <ChevronRight
          className={`size-3.5 shrink-0 self-center text-[color:var(--color-text-dim)] transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
        <Sparkles className="size-4 shrink-0 self-center text-[color:var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold">Company resumes</h2>
        <span className="text-xs text-[color:var(--color-text-dim)]">{resumes.length}</span>
      </button>
      {open && (
        <>
          <p className="mt-1 pl-6 text-xs leading-5 text-[color:var(--color-text-dim)]">
            Tailored for one specific application. Each one is its own resume, not a
            version buried under a shared source.
          </p>
          {resumes.length === 0 ? (
            <div className="workspace-panel mt-3 px-5 py-4 text-xs text-[color:var(--color-text-dim)]">
              No company resumes yet.
            </div>
          ) : (
            <div className="mt-3">
              <CompanyResumesFolder
                resumes={resumes}
                applicationById={applicationById}
                defaultExpanded={resumes.some((r) => r.id === openId)}
              />
            </div>
          )}
        </>
      )}
    </section>
  );
}

/**
 * One source resume's own most recent render as its thumbnail — there's no
 * company logo to stand in for a general-purpose resume, so the document
 * itself is what tells one apart from another.
 */
function SourceResumeCard({
  resume,
  hasOriginal,
  onUseAsTemplate,
  building,
}: {
  resume: Resume;
  hasOriginal: boolean;
  onUseAsTemplate: (resume: Resume) => void;
  building: boolean;
}) {
  const qc = useQueryClient();
  const { data: versions = [] } = useQuery({
    queryKey: ["versions", resume.id],
    queryFn: () => api.listVersions(resume.id),
  });
  const latest = versions[0];
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
  return (
    <div className="flex h-full w-full flex-col gap-1.5 p-2">
      <div className="min-h-0 flex-1">
        <FolderResumeCard
          resume={resume}
          latestVersion={latest}
          versionCount={versions.length}
          subtitle={resume.base_role ?? "Source resume"}
          thumbnail={
            <span className="block h-16 w-12 overflow-hidden rounded-md border border-[color:var(--color-border)] bg-white">
              <ResumeVersionPreview
                downloadUrl={latest ? api.downloadVersionUrl(resume.id, latest.id) : null}
                label={resume.name}
              />
            </span>
          }
        />
      </div>
      <div className="flex shrink-0 items-center justify-center gap-1.5">
        {hasOriginal && (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onUseAsTemplate(resume);
            }}
            disabled={building}
            title="Read this resume's design and save it as a template"
            className="rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-2 py-1 text-[10px] text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)] disabled:opacity-50"
          >
            {building ? "Reading…" : "Use as template"}
          </button>
        )}
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            if (window.confirm(`Archive ${resume.name}? Its versions will remain stored.`)) {
              removeResume.mutate();
            }
          }}
          title="Archive this resume"
          aria-label={`Archive ${resume.name}`}
          className="rounded-full p-1.5 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-rose)]/10 hover:text-[color:var(--color-rose-ink)]"
        >
          <Archive className="size-3" />
        </button>
      </div>
    </div>
  );
}

/** Every general-purpose source resume, stacked inside one folder — the same
 * gallery the company resumes use, so the whole library reads consistently
 * instead of switching visual language section to section. */
function SourceResumesFolder({
  resumes,
  originals,
  onUseAsTemplate,
  buildingId,
  defaultExpanded,
}: {
  resumes: Resume[];
  originals: Record<string, boolean>;
  onUseAsTemplate: (resume: Resume) => void;
  buildingId: string | null;
  defaultExpanded: boolean;
}) {
  return (
    <ProjectFolder
      title="Source resumes"
      description="The data, not the look"
      ariaLabel={`Source resumes — ${resumes.length} resume${resumes.length === 1 ? "" : "s"}`}
      itemLabel="resume"
      count={resumes.length}
      defaultExpanded={defaultExpanded}
      frontVisual={
        <FolderOpen className="size-12 text-[color:var(--color-text-muted)] opacity-40" />
      }
      previews={resumes.map((resume) => ({
        id: resume.id,
        content: (
          <SourceResumeCard
            resume={resume}
            hasOriginal={originals[resume.id] ?? false}
            onUseAsTemplate={onUseAsTemplate}
            building={buildingId === resume.id}
          />
        ),
      }))}
    />
  );
}

function SourceResumesGrid({
  resumes,
  openId,
  originals,
  onUseAsTemplate,
  buildingId,
}: {
  resumes: Resume[];
  openId: string | null;
  originals: Record<string, boolean>;
  onUseAsTemplate: (resume: Resume) => void;
  buildingId: string | null;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="mt-6">
      <button
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex items-baseline gap-2 text-left"
      >
        <ChevronRight
          className={`size-3.5 shrink-0 self-center text-[color:var(--color-text-dim)] transition-transform ${
            open ? "rotate-90" : ""
          }`}
        />
        <FolderOpen className="size-4 shrink-0 self-center text-[color:var(--color-text-muted)]" />
        <h2 className="text-sm font-semibold">Source resumes</h2>
        <span className="text-xs text-[color:var(--color-text-dim)]">{resumes.length}</span>
      </button>
      {!open ? (
        <p className="mt-1 pl-6 text-xs leading-5 text-[color:var(--color-text-dim)]">
          {resumes.length} resume{resumes.length === 1 ? "" : "s"}. Open to see them.
        </p>
      ) : (
        <>
          <p className="mt-1 pl-6 text-xs leading-5 text-[color:var(--color-text-dim)]">
            The data. Tailored versions are saved here, under the resume they came from.
          </p>
          {resumes.length === 0 ? (
            <div className="workspace-panel mt-3 px-5 py-4 text-xs text-[color:var(--color-text-dim)]">
              No resumes yet.
            </div>
          ) : (
            <div className="mt-3">
              <SourceResumesFolder
                resumes={resumes}
                originals={originals}
                onUseAsTemplate={onUseAsTemplate}
                buildingId={buildingId}
                defaultExpanded={resumes.some((r) => r.id === openId)}
              />
            </div>
          )}
        </>
      )}
    </section>
  );
}

/** A safe, meaningful filename when there's no source_filename to reuse —
 * "Daice Labs.pdf", not a UUID fragment nobody chose. */
function fallbackDownloadName(resumeName: string, createdAt: string): string {
  const slug = resumeName.trim().replace(/[^\w\- ]+/g, "").replace(/\s+/g, "_");
  const date = createdAt.slice(0, 10);
  return `${slug || "resume"}_${date}.pdf`;
}

function VersionRow({
  version,
  resumeId,
  resumeName,
  applicationRef,
}: {
  version: ResumeVersionSummary;
  resumeId: string;
  resumeName: string;
  applicationRef: ApplicationRef | undefined;
}) {
  const qc = useQueryClient();
  const downloadUrl = api.downloadVersionUrl(resumeId, version.id);
  const downloadName = version.source_filename ?? fallbackDownloadName(resumeName, version.created_at);
  // An uploaded PDF/DOCX has no structured json_resume to edit — it's the
  // {"uploaded": true, ...} stub the upload endpoints write. Opening it in
  // the structured editor renders an empty form with nothing to click into,
  // which reads as the click having done nothing. source_filename is set
  // only on uploads, so it doubles as that signal.
  const isUploadedFile = !!version.source_filename;
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
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">
              {version.source_filename ?? created}
            </span>
            {applicationRef && (
              <span className="shrink-0 truncate rounded-full bg-[color:var(--color-violet)]/10 px-2 py-0.5 text-[11px] font-medium text-[color:var(--color-violet)]">
                {applicationRef.company ?? applicationRef.jobTitle}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 text-xs text-[color:var(--color-text-dim)]">
            {version.source_filename && <span>{created}</span>}
            {version.source_filename && <span>·</span>}
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
                <span>Match {version.ats_score}</span>
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
        {!isUploadedFile && (
          <Link
            href={`/resumes/${resumeId}/${version.id}`}
            className="kinetic-button kinetic-button-secondary min-h-0 px-3 py-1.5"
          >
            <MessageSquareText className="size-3" />
            Open
          </Link>
        )}
        {version.status === "final" && (
          <button
            onClick={() => downloadPdf(downloadUrl, downloadName)}
            className="kinetic-button kinetic-button-primary min-h-0 px-3 py-1.5"
          >
            <Download className="size-3" />
            Download PDF
          </button>
        )}
        <button
          onClick={() => {
            if (window.confirm("Archive this resume version? It remains stored in the database.")) {
              removeVersion.mutate();
            }
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
