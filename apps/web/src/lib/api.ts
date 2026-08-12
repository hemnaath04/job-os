import type {
  Application,
  AppStatus,
  CalendarEntry,
  DiscoverySearchRequest,
  DiscoverySearchResponse,
  Job,
  MeRead,
  ProfileFact,
  Resume,
  ResumeChatResponse,
  ResumeImportItem,
  JsonResume,
  ResumeReviewResult,
  RevisionMessage,
  ResumeVersion,
  ResumeVersionSummary,
  SavedSearch,
  SmartSearchResponse,
  TailorResponse,
  UserSettings,
  UserSettingsPatch,
} from "./types";
import { appwritePipeline } from "./appwrite/client";
import {
  isAppwritePipelineEnabled,
  isAppwriteWorkspaceEnabled,
} from "./appwrite/config";
import {
  appwriteWorkspace,
  type AgentJob,
} from "./appwrite/workspace";
import { renderResumePreview } from "./resume-preview";
import { withTimeout } from "./async";

/** Body accepted by the /api/discover route handler. */
export type DiscoverNoKeyRequest = {
  sources?: string[];
  title_keywords?: string[];
  location?: string;
  country_codes?: string[];
  max_age_days?: number;
  remote?: boolean;
  limit?: number;
  companies?: string[];
  include_remote_boards?: boolean;
  /**
   * Bring-your-own-key credentials, read from localStorage at call time. They
   * ride along with the request instead of living on the server.
   */
  keys?: {
    jsearch?: string;
    adzuna_app_id?: string;
    adzuna_app_key?: string;
  };
  /**
   * Endpoints the user hosts themselves, read from localStorage at call time
   * and gated behind the acceptance on /jobs/keys. Same rule as the keys: they
   * ride along with the request instead of living on the server.
   */
  custom_sources?: {
    id: string;
    name: string;
    url: string;
    auth_header?: string;
    auth_value?: string;
  }[];
};

/**
 * A render with no review. The model review is the slow part of render-review
 * (~80s on top of a ~17s render), so this endpoint returns just the PDF fast,
 * and the quality review follows separately. Same request shape as
 * render-review, minus the review in the response.
 */
export type RenderResult = {
  latex_source: string;
  pdf_base64: string;
};

/**
 * Result of a finalize attempt. `blocked` is an ordinary outcome, not an error:
 * the review found issues and the caller should show them and offer to proceed
 * anyway, since the review advises rather than gates.
 */
/**
 * What the container returns after turning an upload into a LaTeX template.
 *
 * `attempts` and `repairs` are reported rather than hidden: a template that took
 * three compile-and-repair rounds is worth knowing about, and claiming a clean
 * first pass when there was not one would be a lie the user could not check.
 */
export type GeneratedTemplate = {
  name: string;
  latex_source: string;
  engine: string;
  notes: string;
  pdf_base64: string;
  attempts: number;
  repairs: string[];
};

export type FinalizeOutcome =
  | { status: "finalized"; version: ResumeVersion }
  | { status: "blocked"; review: ResumeReviewResult; version: ResumeVersion };

export type ProfileFactCreate = {
  kind: string;
  title: string;
  org?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  location?: string | null;
  payload?: Record<string, unknown>;
  verified?: boolean;
  source_url?: string | null;
  bullets?: { text: string; target_role?: string | null; metric_verified?: boolean }[];
};

const BASE = "/api/backend";

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    // Read the body once, as text: a Response's body can only be consumed a
    // single time, and a failed res.json() still consumes the stream on its
    // way to throwing. A second read (the old `catch { res.text() }` fallback)
    // then throws "body already used" / "Body is disturbed or locked" and
    // replaces the real error with a confusing one, which is exactly what a
    // non-JSON error page (an upstream's own platform error page, for example)
    // used to trigger here.
    const text = await res.text();
    let detail: unknown = text;
    try {
      detail = JSON.parse(text);
    } catch {
      /* not JSON, keep the raw text */
    }
    throw new Error(`${res.status}: ${JSON.stringify(detail)}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Calls the FastAPI backend through the token-injecting proxy. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return fetchJson<T>(`${BASE}${path}`, init);
}

/**
 * Calls a Next.js route handler in this app rather than the FastAPI backend.
 * The Clerk middleware still gates it, so the auth story is the same.
 */
async function localRequest<T>(path: string, init?: RequestInit): Promise<T> {
  return fetchJson<T>(path, init);
}

const AGENT_POLL_MS = 1_200;
const AGENT_TIMEOUT_MS = 15 * 60 * 1_000;
/**
 * Ceiling for a container round trip that renders a PDF: a cold start plus a
 * LaTeX compile plus a quality-model pass. Generous, but bounded, so a wedged
 * request surfaces as an error the user can retry instead of a spinner that
 * never resolves.
 */
const RENDER_TIMEOUT_MS = 2 * 60 * 1_000;
/**
 * A single conversational edit is one Claude call, but a grounded revision over
 * a full resume routinely runs three to five minutes. Cap it generously so the
 * in-page wait does not surface an error while the job is still landing; the
 * shell's operations tracker also follows the same job independently, so the
 * result is reachable even past this.
 */
const REVISE_TIMEOUT_MS = 8 * 60 * 1_000;
/**
 * The quality review fetches every GitHub README the resume links to, then waits
 * on a model pass over the whole document. Measured runs sit past two minutes,
 * which is why it can no longer run inline: Heroku's router drops a request at 30
 * seconds. As an agent job it has the function's 900s budget behind it, so this
 * only caps how long the page itself waits before pointing at the operations
 * tracker, which follows the same job independently.
 */
const REVIEW_TIMEOUT_MS = 8 * 60 * 1_000;
const WARM_TIMEOUT_MS = 12 * 1_000;
/**
 * Building a template is the slowest call in the app: a cold start, a model
 * pass over the upload, a compile to validate, and repair rounds when the first
 * LaTeX does not compile. Capped just under the proxy route's own maxDuration of
 * 300s, so an overrun surfaces as the message below rather than as whatever the
 * platform returns when it tears a function down mid-flight.
 */
const TEMPLATE_TIMEOUT_MS = 290 * 1_000;

/**
 * Wake the API container before a call that would otherwise pay for the cold
 * start while the user watches a spinner.
 *
 * The keep-warm workflow asks for a ping every five minutes, but GitHub
 * throttles scheduled runs hard: observed gaps are 25 to 55 minutes, so the
 * container is regularly scaled to zero when a review starts. /health is
 * token-free and cheap. Failures are swallowed, since this is only an
 * optimisation and the real call reports its own errors.
 */
async function warmBackend(): Promise<void> {
  try {
    await withTimeout(
      fetch("/api/backend/health", { cache: "no-store" }),
      WARM_TIMEOUT_MS,
      "warm-up timed out",
    );
  } catch {
    /* the real request follows and will surface anything that matters */
  }
}

async function waitForAgentJob<T>(
  job: AgentJob,
  { timeoutMs = AGENT_TIMEOUT_MS }: { timeoutMs?: number } = {},
): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const current = await appwriteWorkspace.getAgentJob<T>(job.id);
    if (current.status === "succeeded") {
      if (current.output === null) {
        throw new Error("Agent completed without returning a result.");
      }
      return current.output;
    }
    if (current.status === "failed") {
      throw new Error(current.error || "The Appwrite agent failed.");
    }
    await new Promise((resolve) => window.setTimeout(resolve, AGENT_POLL_MS));
  }
  throw new Error("The agent is still running. Refresh shortly to see its result.");
}

/**
 * Render a version to PDF, attach it, then score it as an agent job.
 *
 * Every caller that needs a reviewed version wants exactly this sequence, and
 * all three used to get it by calling `/resumes/render-review` inline. That
 * cannot work on Heroku: the endpoint fetches GitHub READMEs and waits on a model
 * pass, so it runs for minutes, and the router hangs up on any request still open
 * at 30 seconds. Every review and finalize since the migration returned a 503
 * while the dyno quietly finished the work and found nobody to hand it to.
 *
 * Split by what each side is actually for. Rendering needs the container's Typst
 * binary and takes well under a second, so it stays a request. Scoring needs
 * time, so it becomes an agent job with the function's 900s budget, which also
 * means it survives a closed tab and shows up in the operations tracker. Handing
 * the rendered PDF over by file id is what lets the function score it at all:
 * before this it reviewed empty bytes and reported "0 pages" every time.
 */
async function renderAndReview(
  versionId: string,
  jsonResume: JsonResume,
  {
    templateId,
    onPdfReady,
  }: { templateId?: string | null; onPdfReady?: () => void } = {},
): Promise<{ version: ResumeVersion; review: ResumeReviewResult }> {
  const draft = await legacyApi.render(jsonResume, { templateId });
  const withPdf = await appwriteWorkspace.attachPdf(versionId, draft);
  onPdfReady?.();
  const pdfFileId = (withPdf as ResumeVersion & { pdf_file_id?: string | null })
    .pdf_file_id;
  if (!pdfFileId) {
    throw new Error("The rendered PDF could not be attached for review.");
  }
  const job = await appwriteWorkspace.reviewResume(versionId, pdfFileId);
  const output = await waitForAgentJob<{
    version: ResumeVersion;
    review: ResumeReviewResult;
  }>(job, { timeoutMs: REVIEW_TIMEOUT_MS });
  return {
    version: appwriteWorkspace.registerVersionFile(output.version),
    review: output.review,
  };
}

// The browser no longer ships verified facts up for review. It had to when the
// reviewer was the stateless container, which has no workspace to read them from,
// and getting them wrong scored an honest resume in the twenties by reading real
// history as invented. The agent function reads the vault server-side, so the
// facts now come from the same place the tailor already reads them.

const legacyApi = {
  listApplications: (params?: { status?: AppStatus; q?: string }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.q) qs.set("q", params.q);
    return request<Application[]>(`/applications?${qs.toString()}`);
  },
  patchApplication: (id: string, body: Partial<Application>) =>
    request<Application>(`/applications/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  createApplication: (body: { job_id: string; status?: AppStatus; notes?: string }) =>
    request<Application>("/applications", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  archiveApplication: (id: string) =>
    request<void>(`/applications/${id}`, { method: "DELETE" }),

  listJobs: () => request<Job[]>("/jobs"),
  getJob: (id: string) => request<Job>(`/jobs/${id}`),
  jobFromUrl: (url: string) =>
    request<Job>("/jobs/from-url", { method: "POST", body: JSON.stringify({ url }) }),
  jobFromText: (jd_text: string, company_hint?: string) =>
    request<Job>("/jobs/from-text", {
      method: "POST",
      body: JSON.stringify({ jd_text, company_hint }),
    }),

  listFacts: (kind?: string) => {
    const qs = new URLSearchParams();
    if (kind) qs.set("kind", kind);
    return request<ProfileFact[]>(`/profile/facts${qs.toString() ? "?" + qs : ""}`);
  },
  deleteFact: (id: string) =>
    request<void>(`/profile/facts/${id}`, { method: "DELETE" }),

  uploadResumeForm: async (file: File, opts?: { replaceExisting?: boolean }) => {
    const fd = new FormData();
    fd.append("file", file);
    const qs = new URLSearchParams();
    if (opts?.replaceExisting) qs.set("replace_existing", "true");
    const res = await fetch(
      `/api/backend/profile/upload-resume${qs.toString() ? "?" + qs : ""}`,
      { method: "POST", body: fd, cache: "no-store" },
    );
    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    return (await res.json()) as {
      facts_created: number;
      facts_skipped: number;
      bullets_created: number;
      notes: string[];
    };
  },

  listResumes: () => request<Resume[]>("/resumes"),
  createResume: (body: { name: string; base_role?: string | null; is_master?: boolean }) =>
    request<Resume>("/resumes", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateResume: (resumeId: string, body: { name?: string; base_role?: string | null }) =>
    request<Resume>(`/resumes/${resumeId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteResume: (resumeId: string) =>
    request<void>(`/resumes/${resumeId}`, { method: "DELETE" }),
  importResumes: async (
    files: File[],
    sourceLabel = "iCloud Drive",
    masterFilename?: string,
  ) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    body.append("source_label", sourceLabel);
    if (masterFilename) body.append("master_filename", masterFilename);
    const response = await fetch("/api/backend/resumes/import", {
      method: "POST",
      body,
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
    return (await response.json()) as { items: ResumeImportItem[] };
  },
  listVersions: (resumeId: string) =>
    request<ResumeVersionSummary[]>(`/resumes/${resumeId}/versions`),
  getVersion: (resumeId: string, versionId: string) =>
    request<ResumeVersion>(`/resumes/${resumeId}/versions/${versionId}`),
  approveVersion: (resumeId: string, versionId: string) =>
    request<ResumeVersion>(`/resumes/${resumeId}/versions/${versionId}/approve`, {
      method: "POST",
    }),
  editVersion: (resumeId: string, versionId: string, jsonResume: object, note: string) =>
    request<ResumeVersion>(`/resumes/${resumeId}/versions/${versionId}/edit`, {
      method: "POST",
      body: JSON.stringify({ json_resume: jsonResume, note }),
    }),
  deleteVersion: (resumeId: string, versionId: string) =>
    request<void>(`/resumes/${resumeId}/versions/${versionId}`, { method: "DELETE" }),
  reviewVersion: (resumeId: string, versionId: string) =>
    request<ResumeReviewResult>(`/resumes/${resumeId}/versions/${versionId}/review`, {
      method: "POST",
    }),
  chatEditVersion: (
    resumeId: string,
    versionId: string,
    message: string,
    apply = true,
  ) =>
    request<ResumeChatResponse>(`/resumes/${resumeId}/versions/${versionId}/chat`, {
      method: "POST",
      body: JSON.stringify({ message, apply }),
    }),
  applyRevisionProposal: (
    resumeId: string,
    versionId: string,
    messageId: string,
  ) =>
    request<ResumeChatResponse>(
      `/resumes/${resumeId}/versions/${versionId}/messages/${messageId}/apply`,
      { method: "POST" },
    ),
  listRevisionMessages: (resumeId: string, versionId: string) =>
    request<RevisionMessage[]>(
      `/resumes/${resumeId}/versions/${versionId}/messages`,
    ),
  finalizeVersion: (resumeId: string, versionId: string) =>
    request<ResumeVersion>(`/resumes/${resumeId}/versions/${versionId}/finalize`, {
      method: "POST",
    }),
  downloadVersionUrl: (resumeId: string, versionId: string) =>
    `/api/backend/resumes/${resumeId}/versions/${versionId}/download`,
  previewVersionUrl: (resumeId: string, versionId: string) =>
    `/api/backend/resumes/${resumeId}/versions/${versionId}/preview`,
  previewDraft: async (jsonResume: object) => {
    const response = await fetch("/api/backend/resumes/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ json_resume: jsonResume }),
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
    return response.text();
  },

  tailorResume: (resumeId: string, jobId: string) =>
    request<TailorResponse>(`/resumes/${resumeId}/versions/tailor`, {
      method: "POST",
      body: JSON.stringify({ job_id: jobId }),
    }),

  // No renderReviewDraft here any more. It called /resumes/render-review, which
  // cannot complete on Heroku: the review runs for minutes and the router hangs
  // up at 30 seconds, so every caller got a 503. The review is an agent job now,
  // and the verified facts it needs are read server-side from the real vault
  // rather than shipped up from the browser. See renderAndReview above.

  /**
   * Render an unsaved document to a PDF on the container without the model
   * review. The container is the only runtime with the engine, and this is the
   * fast half: it answers in well under a second, so Download lights up straight
   * away while the review is scored separately as an agent job.
   */
  render: async (
    jsonResume: object,
    { templateId }: { templateId?: string | null } = {},
  ) => {
    const look = templateId
      ? await appwriteWorkspace.getTemplateSource(templateId)
      : null;
    await warmBackend();
    return withTimeout(
      request<RenderResult>("/resumes/render", {
        method: "POST",
        body: JSON.stringify({
          json_resume: jsonResume,
          template_key: look?.template_key ?? null,
          latex_source: look?.latex_source ?? null,
        }),
      }),
      RENDER_TIMEOUT_MS,
      "The PDF render timed out. The API container may be waking up, try again in a moment.",
    );
  },

  listCalendar: (params?: { days?: number; include_past?: number }) => {
    const qs = new URLSearchParams();
    if (params?.days !== undefined) qs.set("days", String(params.days));
    if (params?.include_past !== undefined)
      qs.set("include_past", String(params.include_past));
    return request<CalendarEntry[]>(
      `/calendar/upcoming${qs.toString() ? "?" + qs : ""}`,
    );
  },

  getMe: () => request<MeRead>("/me"),
  getSettings: () => request<UserSettings>("/me/settings"),
  patchSettings: (body: UserSettingsPatch) =>
    request<UserSettings>("/me/settings", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  discoverySearch: (body: DiscoverySearchRequest) =>
    request<DiscoverySearchResponse>("/discovery/search", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /**
   * Key-free sources (public ATS boards + remote feeds). Served by this app's
   * own /api/discover route, not by FastAPI, so it works with no credits and
   * no server-side keys.
   */
  discoverNoKey: (body: DiscoverNoKeyRequest) =>
    localRequest<DiscoverySearchResponse>("/api/discover", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  discoveryImport: (body: {
    source: string;
    source_id: string;
    source_url: string;
    title: string;
    description: string;
    company_name?: string | null;
    company_domain?: string | null;
    location?: string | null;
    posted_at?: string | null;
  }) =>
    request<Job>("/discovery/import", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listSavedSearches: () => request<SavedSearch[]>("/discovery/saved"),
  createSavedSearch: (body: { name: string; query: DiscoverySearchRequest }) =>
    request<SavedSearch>("/discovery/saved", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteSavedSearch: (id: string) =>
    request<void>(`/discovery/saved/${id}`, { method: "DELETE" }),
  runSavedSearch: (id: string) =>
    request<DiscoverySearchResponse>(`/discovery/saved/${id}/run`, { method: "POST" }),
  smartSearch: (query: string) =>
    request<SmartSearchResponse>("/discovery/smart-search", {
      method: "POST",
      body: JSON.stringify({ query }),
    }),

  createFact: (body: ProfileFactCreate) =>
    request<ProfileFact>("/profile/facts", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

export const api = {
  ...legacyApi,

  listFacts: (kind?: string) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.listFacts(kind)
      : legacyApi.listFacts(kind),

  deleteFact: (id: string) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.archiveFact(id)
      : legacyApi.deleteFact(id),

  createFact: (body: ProfileFactCreate) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.createFact(body)
      : legacyApi.createFact(body),

  async uploadResumeForm(
    file: File,
    opts?: { replaceExisting?: boolean },
  ) {
    if (!isAppwriteWorkspaceEnabled) {
      return legacyApi.uploadResumeForm(file, opts);
    }
    const job = await appwriteWorkspace.extractProfile(file);
    return waitForAgentJob<{
      facts_created: number;
      facts_skipped: number;
      bullets_created: number;
      notes: string[];
    }>(job);
  },

  listResumes: () =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.listResumes()
      : legacyApi.listResumes(),

  createResume: ({
    job_posting_id,
    ...body
  }: {
    name: string;
    base_role?: string | null;
    is_master?: boolean;
    job_posting_id?: string | null;
  }) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.createResume({ ...body, job_posting_id })
      : // The Postgres resumes table has no job column, so the legacy path only
        // gets the fields it knows and falls back to name matching for dedupe.
        legacyApi.createResume(body),

  updateResume: (
    resumeId: string,
    body: { name?: string; base_role?: string | null },
  ) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.updateResume(resumeId, body)
      : legacyApi.updateResume(resumeId, body),

  deleteResume: (resumeId: string) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.archiveResume(resumeId)
      : legacyApi.deleteResume(resumeId),

  /**
   * Templates are look-only rows. Saving a resume's look creates one and leaves
   * the resume, its versions and its data completely untouched, so undoing it is
   * just archiving the template. Appwrite only: Postgres has no templates.
   */
  listTemplates: () => {
    if (!isAppwriteWorkspaceEnabled) return Promise.resolve([]);
    return appwriteWorkspace.listTemplates();
  },

  /**
   * Turn an uploaded .tex or .pdf into a template, then store it.
   *
   * The work runs on the API container, which has the LaTeX engine needed to
   * prove the template compiles before it is kept. An upload that cannot be
   * turned into a template that compiles surfaces as an error and nothing is
   * stored, so the user keeps whatever templates they already had.
   *
   * A .tex keeps the design exactly. A .pdf is a reconstruction: the model reads
   * the page and writes LaTeX aiming at the same design, which gets close and is
   * not a copy.
   */
  async buildLatexTemplate(
    file: File,
    {
      name,
      createdFromResumeId,
    }: { name?: string; createdFromResumeId?: string | null } = {},
  ) {
    if (!isAppwriteWorkspaceEnabled) {
      throw new Error("Templates require the Appwrite workspace.");
    }
    const body = new FormData();
    body.append("file", file);
    if (name) body.append("name", name);
    await warmBackend();
    const response = await withTimeout(
      fetch(`${BASE}/resumes/latex-template`, {
        method: "POST",
        body,
        cache: "no-store",
      }),
      TEMPLATE_TIMEOUT_MS,
      "Building the template ran out of time. A .tex upload is much faster than a " +
        "PDF, and keeps the design exactly, so try that if you have the source.",
    );
    if (!response.ok) {
      let detail = `${response.status}`;
      try {
        const parsed = (await response.json()) as { detail?: string };
        if (parsed.detail) detail = parsed.detail;
      } catch {
        /* keep the status code */
      }
      throw new Error(detail);
    }
    const generated = (await response.json()) as GeneratedTemplate;
    const stored = await appwriteWorkspace.createLatexTemplate({
      name: generated.name,
      latex_source: generated.latex_source,
      notes: generated.notes,
      pdf_base64: generated.pdf_base64,
      created_from_resume_id: createdFromResumeId ?? null,
    });
    return { template: stored, attempts: generated.attempts, repairs: generated.repairs };
  },

  /**
   * Build a template from the document a resume was originally imported from.
   *
   * The same path as an upload, pointed at a file already in the library. That
   * file is a PDF, so this is the reconstruction path rather than the exact one.
   * Returns null when the resume has no original document, so the caller can say
   * why instead of quietly producing a copy of some other look.
   */
  async buildLatexTemplateFromResume(resume: Resume) {
    if (!isAppwriteWorkspaceEnabled) {
      throw new Error("Templates require the Appwrite workspace.");
    }
    const original = await appwriteWorkspace.findResumeOriginalDocument(resume.id);
    if (!original) return null;
    const file = await appwriteWorkspace.downloadStoredFile(
      original.fileId,
      original.filename,
    );
    return api.buildLatexTemplate(file, { createdFromResumeId: resume.id });
  },

  archiveTemplate: (templateId: string) => {
    if (!isAppwriteWorkspaceEnabled) {
      throw new Error("Templates require the Appwrite workspace.");
    }
    return appwriteWorkspace.archiveTemplate(templateId);
  },

  async importResumes(
    files: File[],
    sourceLabel = "Resume library",
    masterFilename?: string,
  ) {
    if (!isAppwriteWorkspaceEnabled) {
      return legacyApi.importResumes(files, sourceLabel, masterFilename);
    }
    const items: ResumeImportItem[] = [];
    for (const file of files) {
      try {
        const job = await appwriteWorkspace.uploadResume(
          file,
          file.name === masterFilename,
        );
        const output = await waitForAgentJob<{
          resume: Resume;
          version: ResumeVersion;
        }>(job);
        items.push({
          filename: file.name,
          resume_id: output.resume.id,
          version_id: output.version.id,
          imported: true,
          is_master: output.resume.is_master,
          note: "Imported into Appwrite.",
        });
      } catch (error) {
        items.push({
          filename: file.name,
          resume_id: null,
          version_id: null,
          imported: false,
          is_master: file.name === masterFilename,
          note: error instanceof Error ? error.message : "Import failed",
        });
      }
    }
    return { items };
  },

  listVersions: (resumeId: string) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.listVersions(resumeId)
      : legacyApi.listVersions(resumeId),

  getVersion: (resumeId: string, versionId: string) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.getVersion(versionId)
      : legacyApi.getVersion(resumeId, versionId),

  approveVersion: (resumeId: string, versionId: string) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.approveVersion(versionId)
      : legacyApi.approveVersion(resumeId, versionId),

  editVersion: (
    resumeId: string,
    versionId: string,
    jsonResume: object,
    note: string,
  ) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.editVersion(
          versionId,
          jsonResume as import("./types").JsonResume,
          note,
        )
      : legacyApi.editVersion(resumeId, versionId, jsonResume, note),

  deleteVersion: (resumeId: string, versionId: string) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.archiveVersion(versionId)
      : legacyApi.deleteVersion(resumeId, versionId),

  /**
   * Review a stored Appwrite version on the FastAPI container.
   *
   * This used to queue the Appwrite agent function, which renders the PDF
   * before reviewing and therefore always failed there: the runtime has no
   * pango or cairo for WeasyPrint. The container has them, so send the document
   * to the stateless endpoint and persist the review and PDF back to Appwrite.
   */
  async reviewVersion(
    resumeId: string,
    versionId: string,
    { templateId }: { templateId?: string | null } = {},
  ) {
    if (!isAppwriteWorkspaceEnabled) {
      return legacyApi.reviewVersion(resumeId, versionId);
    }
    const version = await appwriteWorkspace.getVersion(versionId);
    const { review } = await renderAndReview(versionId, version.json_resume, {
      templateId,
    });
    return review;
  },

  /**
   * Render, attach and score a version, returning the updated row as well.
   *
   * For callers that already hold the document and need the reviewed row back,
   * rather than just the score `reviewVersion` returns. The tailor page is the
   * one: it folds the result straight into the page it is already showing.
   */
  renderAndReviewVersion(
    versionId: string,
    jsonResume: JsonResume,
    { templateId }: { templateId?: string | null } = {},
  ) {
    return renderAndReview(versionId, jsonResume, { templateId });
  },

  async chatEditVersion(
    resumeId: string,
    versionId: string,
    message: string,
    apply = true,
  ) {
    if (!isAppwriteWorkspaceEnabled) {
      return legacyApi.chatEditVersion(resumeId, versionId, message, apply);
    }
    const job = await appwriteWorkspace.reviseResume(
      resumeId,
      versionId,
      message,
    );
    // A single conversational edit is one Claude call. Waiting the full
    // fifteen-minute batch budget on it is indistinguishable from a hang.
    const output = await waitForAgentJob<{
      message: string;
      suggestions: string[];
      proposal_id: string;
      proposed_json_resume: import("./types").JsonResume;
      // Present once the revise agent reports which unsupported claims it left
      // out. Optional so an older agent build that omits it still typechecks and
      // simply surfaces no blocked claims.
      blocked_claims?: import("./types").BlockedClaim[];
    }>(job, { timeoutMs: REVISE_TIMEOUT_MS });
    return {
      ...output,
      blocked_claims: output.blocked_claims ?? [],
      version: null,
      review: null,
    } satisfies ResumeChatResponse;
  },

  async applyRevisionProposal(
    resumeId: string,
    versionId: string,
    messageId: string,
  ) {
    if (!isAppwriteWorkspaceEnabled) {
      return legacyApi.applyRevisionProposal(resumeId, versionId, messageId);
    }
    const proposal = await appwriteWorkspace.applyRevisionProposal(
      resumeId,
      versionId,
      messageId,
    );
    if (!proposal.version) return proposal;
    const { version: reviewed, review } = await renderAndReview(
      proposal.version.id,
      proposal.version.json_resume,
    );
    return { ...proposal, version: reviewed, review };
  },

  listRevisionMessages: (resumeId: string, versionId: string) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.listMessages(versionId)
      : legacyApi.listRevisionMessages(resumeId, versionId),

  /**
   * Finalize a version: re-review it on the container, then mark it final.
   *
   * The review is advisory, not a gate. This is the user's own resume tool, and
   * an honest resume routinely scores in the seventies, so refusing to finalize
   * anything short of a pass meant the user could never finalize at all. A
   * failing review comes back as `blocked` with the score and issues so the
   * caller can show them and offer to proceed; calling again with `force` marks
   * it final regardless. The PDF and review are written either way.
   */
  async finalizeVersion(
    resumeId: string,
    versionId: string,
    {
      force = false,
      templateId,
      onPdfReady,
    }: {
      force?: boolean;
      templateId?: string | null;
      /**
       * Fired the moment the PDF is rendered and attached, before the slower
       * review runs. Lets the caller light up Download at ~17s instead of
       * waiting the full ~100s. Only called on the render path, never when a
       * stored review is reused.
       */
      onPdfReady?: () => void;
    } = {},
  ): Promise<FinalizeOutcome> {
    if (!isAppwriteWorkspaceEnabled) {
      return {
        status: "finalized",
        version: await legacyApi.finalizeVersion(resumeId, versionId),
      };
    }
    const version = await appwriteWorkspace.getVersion(versionId);
    const stored = version as ResumeVersion & { pdf_file_id?: string | null };

    // A stored review is always current: any edit spawns a fresh version with a
    // null review, so a version that still carries one was reviewed against
    // exactly this document, and its PDF is already attached. Reuse it instead
    // of paying ~100s to render and score an unchanged doc again.
    if (stored.review_report && stored.pdf_file_id) {
      if (!stored.review_report.passed && !force) {
        return { status: "blocked", review: stored.review_report, version };
      }
      return {
        status: "finalized",
        version: await appwriteWorkspace.markFinalized(versionId),
      };
    }

    // A forced finalize follows a blocked one, which already rendered and
    // attached the PDF, so honour the decision without a second render.
    if (force && stored.pdf_file_id) {
      return {
        status: "finalized",
        version: await appwriteWorkspace.markFinalized(versionId),
      };
    }

    // No current review. Render the PDF on its own first (~17s) and attach it so
    // Download lights up right away, then run the authoritative review (~80s)
    // that decides whether this finalizes. The fast PDF is a convenience and
    // does not weaken the review gate: the same review still runs and still
    // blocks a failing finalize.
    // The authoritative review. It persists the score onto the version itself, so
    // there is no attachReview to follow: the row the job returns already has it.
    const { version: reviewed, review } = await renderAndReview(
      versionId,
      version.json_resume,
      { templateId, onPdfReady },
    );
    if (!review.passed && !force) {
      return { status: "blocked", review, version: reviewed };
    }
    return {
      status: "finalized",
      version: await appwriteWorkspace.markFinalized(versionId),
    };
  },

  async tailorResume(resumeId: string, jobId: string): Promise<TailorResponse> {
    if (!isAppwriteWorkspaceEnabled) {
      return legacyApi.tailorResume(resumeId, jobId);
    }
    // Job postings still live in Postgres, so fetch the JD here and hand it to
    // the Appwrite tailor agent, which has the resume + verified facts but not
    // the job posting.
    const job = await legacyApi.getJob(jobId);
    const agentJob = await appwriteWorkspace.tailorResume(
      resumeId,
      jobId,
      (job.jd_parsed ?? {}) as Record<string, unknown>,
      "",
    );
    const version = await waitForAgentJob<TailorResponse>(agentJob);
    appwriteWorkspace.registerVersionFile(version);
    return version;
  },

  downloadVersionUrl: (resumeId: string, versionId: string) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.downloadVersionUrl(versionId)
      : legacyApi.downloadVersionUrl(resumeId, versionId),

  previewVersionUrl: (resumeId: string, versionId: string) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.downloadVersionUrl(versionId)
      : legacyApi.previewVersionUrl(resumeId, versionId),

  previewDraft: (jsonResume: object) =>
    isAppwriteWorkspaceEnabled
      ? Promise.resolve(
          renderResumePreview(
            jsonResume as import("./types").JsonResume,
          ),
        )
      : legacyApi.previewDraft(jsonResume),

  async listCalendar(params?: { days?: number; include_past?: number }) {
    if (!isAppwritePipelineEnabled) {
      return legacyApi.listCalendar(params);
    }
    const applications = await appwritePipeline.listApplications();
    const now = Date.now();
    const start =
      now - (params?.include_past ?? 0) * 24 * 60 * 60 * 1_000;
    const end = now + (params?.days ?? 30) * 24 * 60 * 60 * 1_000;
    return applications
      .filter((application) => {
        const timestamp = application.next_action_at
          ? Date.parse(application.next_action_at)
          : Number.NaN;
        return Number.isFinite(timestamp) && timestamp >= start && timestamp <= end;
      })
      .map(
        (application): CalendarEntry => ({
          application_id: application.id,
          when: application.next_action_at as string,
          label: application.next_action_label || "Follow up",
          status: application.status,
          job_id: application.job.id,
          job_title: application.job.title,
          company_name: application.job.company?.name ?? null,
        }),
      )
      .sort((left, right) => left.when.localeCompare(right.when));
  },

  listApplications: (params?: { status?: AppStatus; q?: string }) =>
    isAppwritePipelineEnabled
      ? appwritePipeline.listApplications(params)
      : legacyApi.listApplications(params),

  async patchApplication(id: string, body: Partial<Application>) {
    if (!isAppwritePipelineEnabled) {
      return legacyApi.patchApplication(id, body);
    }

    const updated = await appwritePipeline.patchApplication(id, body);
    return updated;
  },

  async createApplication(body: {
    job_id: string;
    status?: AppStatus;
    notes?: string;
  }) {
    const application = await legacyApi.createApplication(body);
    if (isAppwritePipelineEnabled) {
      try {
        await appwritePipeline.createApplicationCard(application);
      } catch (error) {
        // The durable Neon row exists; the repair import can safely replay it.
        console.error("[pipeline-dual-write] Appwrite create failed", {
          id: application.id,
          error,
        });
      }
    }
    return application;
  },

  async archiveApplication(id: string) {
    if (!isAppwritePipelineEnabled) {
      return legacyApi.archiveApplication(id);
    }

    await appwritePipeline.archiveApplication(id);
  },
};
