"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import {
  Archive,
  ArrowDownWideNarrow,
  CheckCircle2,
  Crown,
  Download,
  FolderOpen,
  FileUp,
  FileText,
  LayoutTemplate,
  LibraryBig,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { ProjectFolder } from "@/components/project-folder";
import { ResumeVersionPreview } from "@/components/resume-version-preview";
import { TemplateDetailDialog } from "@/components/template-picker";
import { TemplatePreview } from "@/components/template-preview";
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

          {/* One shape for the whole library: every category is the same
              folder, side by side, rather than a master row, a single
              company folder, and two differently-styled accordions. */}
          <div className="mt-6 flex flex-wrap gap-5">
            {master && (
              <MasterResumeFolder
                resume={master}
                hasOriginal={originals[master.id] ?? false}
                onUseAsTemplate={(resume) => buildFromResume.mutate(resume)}
                building={buildFromResume.isPending && buildFromResume.variables?.id === master.id}
                defaultExpanded={master.id === openId}
              />
            )}

            <CompanyResumesFolder
              resumes={companyResumes}
              applicationById={applicationById}
              defaultExpanded={companyResumes.some((r) => r.id === openId)}
            />

            <TemplatesFolder
              templates={templates}
              onRemove={(id) => removeTemplate.mutate(id)}
              removingId={
                removeTemplate.isPending ? (removeTemplate.variables ?? null) : null
              }
              onUpload={(file) => buildFromUpload.mutate(file)}
              uploading={buildFromUpload.isPending}
              building={buildFromResume.isPending}
            />

            <SourceResumesFolder
              resumes={sorted}
              originals={originals}
              onUseAsTemplate={(resume) => buildFromResume.mutate(resume)}
              buildingId={
                buildFromResume.isPending
                  ? (buildFromResume.variables?.id ?? null)
                  : null
              }
              defaultExpanded={sorted.some((r) => r.id === openId)}
            />
          </div>
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
 * A generic "there's one more action" tile for a folder overlay — a dashed
 * placeholder card among the real previews, rather than a control bolted
 * onto the outside of the folder. Used for "Use as template" (master) and
 * "Add your own" (templates), so the folder stays the one interactive shape.
 */
function ActionTile({
  icon: Icon,
  label,
  onClick,
  disabled,
}: {
  icon: typeof FileUp;
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="flex h-full w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-[color:var(--color-border)] p-3 text-center text-[color:var(--color-text-dim)] transition hover:border-[color:var(--color-border-strong)] hover:text-[color:var(--color-text)] disabled:opacity-50"
    >
      <Icon className="size-5" />
      <span className="text-xs">{label}</span>
    </button>
  );
}

/** One version of the master, as a folder preview — thumbnail is its own
 * render, since there's no company logo to stand in for it. */
function MasterVersionPreviewCard({
  resumeId,
  resumeName,
  version,
}: {
  resumeId: string;
  resumeName: string;
  version: ResumeVersionSummary;
}) {
  const isUploadedFile = !!version.source_filename;
  const openHref = !isUploadedFile ? `/resumes/${resumeId}/${version.id}` : null;
  const downloadUrl = api.downloadVersionUrl(resumeId, version.id);
  const downloadName = version.source_filename ?? fallbackDownloadName(resumeName, version.created_at);
  const label = version.source_filename
    ? version.source_filename.replace(/\.pdf$/i, "").replace(/_/g, " ")
    : format(new Date(version.created_at), "MMM d, yyyy");
  const identity = (
    <>
      <span className="block h-16 w-12 overflow-hidden rounded-md border border-[color:var(--color-border)] bg-white">
        <ResumeVersionPreview downloadUrl={downloadUrl} label={label} />
      </span>
      <p className="w-full truncate text-center text-[11px] font-medium text-[color:var(--color-text-muted)]">
        {label}
      </p>
    </>
  );
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-3">
      {openHref ? (
        <Link
          href={openHref}
          className="flex w-full flex-1 flex-col items-center justify-center gap-2 rounded-lg transition hover:bg-[color:var(--color-surface-hover)]"
        >
          {identity}
        </Link>
      ) : (
        <div className="flex w-full flex-1 flex-col items-center justify-center gap-2">{identity}</div>
      )}
      {version.status === "final" && (
        <button
          type="button"
          onClick={() => downloadPdf(downloadUrl, downloadName)}
          aria-label="Download PDF"
          title="Download PDF"
          className="flex size-8 shrink-0 items-center justify-center rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
        >
          <Download className="size-3.5" />
        </button>
      )}
    </div>
  );
}

/**
 * The master, the same folder shape as every other category — its previews
 * are its own versions rather than other resumes, since there's only ever
 * one of it. "Use as template" reads the original document regardless of
 * version, so it sits as its own tile rather than on any one version.
 */
function MasterResumeFolder({
  resume,
  hasOriginal,
  onUseAsTemplate,
  building,
  defaultExpanded,
}: {
  resume: Resume;
  hasOriginal: boolean;
  onUseAsTemplate: (resume: Resume) => void;
  building: boolean;
  defaultExpanded: boolean;
}) {
  const { data: versions = [] } = useQuery({
    queryKey: ["versions", resume.id],
    queryFn: () => api.listVersions(resume.id),
  });
  return (
    <ProjectFolder
      title="Master resume"
      description={resume.base_role ?? "The canonical you"}
      ariaLabel={`Master resume — ${versions.length} version${versions.length === 1 ? "" : "s"}`}
      itemLabel="version"
      count={versions.length}
      defaultExpanded={defaultExpanded}
      frontVisual={<Crown className="size-12 text-[color:var(--color-amber)] opacity-60" />}
      previews={[
        ...versions.map((version) => ({
          id: version.id,
          content: (
            <MasterVersionPreviewCard resumeId={resume.id} resumeName={resume.name} version={version} />
          ),
        })),
        ...(hasOriginal
          ? [
              {
                id: "__use_as_template__",
                content: (
                  <ActionTile
                    icon={LayoutTemplate}
                    label={building ? "Reading…" : "Use as template"}
                    onClick={() => onUseAsTemplate(resume)}
                    disabled={building}
                  />
                ),
              },
            ]
          : []),
      ]}
    />
  );
}

/**
 * One template as a folder preview: the real sample render, not an icon.
 * Opening it shows the full render plus where the design came from and what
 * it costs (`TemplateDetailDialog`, shared with the old picker). Removable
 * ones get their own small control; builtins never do.
 */
function TemplatePreviewCard({
  template,
  onOpenDetail,
  onRemove,
  removing,
}: {
  template: ResumeTemplate;
  onOpenDetail: () => void;
  onRemove?: () => void;
  removing: boolean;
}) {
  return (
    <div className="flex h-full w-full flex-col gap-1.5 p-2">
      <button
        type="button"
        onClick={onOpenDetail}
        className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2 rounded-lg p-1 text-center transition hover:bg-[color:var(--color-surface-hover)]"
      >
        <span className="block aspect-[8.5/11] h-20 w-[3.9rem] overflow-hidden rounded-md border border-[color:var(--color-border)] bg-white">
          <TemplatePreview template={template} />
        </span>
        <span className="w-full min-w-0">
          <span className="block truncate text-sm font-semibold">{template.name}</span>
          {template.columns === 2 && (
            <span className="text-[10px] text-[color:var(--color-amber-ink,var(--color-text-muted))]">
              Two column
            </span>
          )}
        </span>
      </button>
      {onRemove && (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onRemove();
          }}
          disabled={removing}
          title="Remove this template"
          aria-label={`Remove ${template.name}`}
          className="shrink-0 self-center rounded-full p-1.5 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-rose)]/10 hover:text-[color:var(--color-rose-ink)] disabled:opacity-50"
        >
          <Trash2 className="size-3" />
        </button>
      )}
    </div>
  );
}

/**
 * The looks available to render with: the six that ship with the app, plus any
 * built from the user's own uploads. A template holds LaTeX only, never resume
 * data. Same folder shape as everything else in the library; the upload
 * control lives as its own tile inside, rather than bolted onto the outside.
 */
function TemplatesFolder({
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
  const [previewing, setPreviewing] = useState<ResumeTemplate | null>(null);
  const uploadInput = useRef<HTMLInputElement>(null);
  const busy = uploading || building;
  const mine = templates.filter((template) => template.kind === "custom").length;

  return (
    <>
      <ProjectFolder
        title="Templates"
        description={busy ? "Writing the LaTeX and compiling it…" : "The look, not the data"}
        ariaLabel={`Templates — ${templates.length}${mine > 0 ? `, ${mine} yours` : ""}`}
        itemLabel="template"
        count={templates.length}
        frontVisual={
          <LayoutTemplate className="size-12 text-[color:var(--color-text-muted)] opacity-40" />
        }
        previews={[
          ...templates.map((template) => ({
            id: template.id,
            content: (
              <TemplatePreviewCard
                template={template}
                onOpenDetail={() => setPreviewing(template)}
                onRemove={
                  template.kind === "custom"
                    ? () => {
                        if (
                          window.confirm(
                            `Remove the ${template.name} template? This deletes the saved look only. No resume, version or PDF is touched.`,
                          )
                        ) {
                          onRemove(template.id);
                        }
                      }
                    : undefined
                }
                removing={removingId === template.id}
              />
            ),
          })),
          {
            id: "__add_template__",
            content: (
              <ActionTile
                icon={FileUp}
                label="Add your own"
                onClick={() => uploadInput.current?.click()}
                disabled={busy}
              />
            ),
          },
        ]}
      />
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
      <TemplateDetailDialog template={previewing} onOpenChange={(open) => !open && setPreviewing(null)} />
    </>
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

/** A safe, meaningful filename when there's no source_filename to reuse —
 * "Daice Labs.pdf", not a UUID fragment nobody chose. */
function fallbackDownloadName(resumeName: string, createdAt: string): string {
  const slug = resumeName.trim().replace(/[^\w\- ]+/g, "").replace(/\s+/g, "_");
  const date = createdAt.slice(0, 10);
  return `${slug || "resume"}_${date}.pdf`;
}
