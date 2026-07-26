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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text();
    }
    throw new Error(`${res.status}: ${JSON.stringify(detail)}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const AGENT_POLL_MS = 1_200;
const AGENT_TIMEOUT_MS = 15 * 60 * 1_000;

async function waitForAgentJob<T>(job: AgentJob): Promise<T> {
  const deadline = Date.now() + AGENT_TIMEOUT_MS;
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
  jobFromUrl: (url: string) =>
    request<Job>("/jobs/from-url", { method: "POST", body: JSON.stringify({ url }) }),

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

  createResume: (body: {
    name: string;
    base_role?: string | null;
    is_master?: boolean;
  }) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.createResume(body)
      : legacyApi.createResume(body),

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

  async reviewVersion(resumeId: string, versionId: string) {
    if (!isAppwriteWorkspaceEnabled) {
      return legacyApi.reviewVersion(resumeId, versionId);
    }
    const job = await appwriteWorkspace.reviewResume(resumeId, versionId);
    const output = await waitForAgentJob<{
      version: ResumeVersion;
      review: ResumeReviewResult;
    }>(job);
    return output.review;
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
    const output = await waitForAgentJob<{
      message: string;
      suggestions: string[];
      proposal_id: string;
      proposed_json_resume: import("./types").JsonResume;
    }>(job);
    return {
      ...output,
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
    const job = await appwriteWorkspace.reviewResume(
      resumeId,
      proposal.version.id,
    );
    const output = await waitForAgentJob<{
      version: ResumeVersion;
      review: ResumeReviewResult;
    }>(job);
    return {
      ...proposal,
      version: output.version,
      review: output.review,
    };
  },

  listRevisionMessages: (resumeId: string, versionId: string) =>
    isAppwriteWorkspaceEnabled
      ? appwriteWorkspace.listMessages(versionId)
      : legacyApi.listRevisionMessages(resumeId, versionId),

  async finalizeVersion(resumeId: string, versionId: string) {
    if (!isAppwriteWorkspaceEnabled) {
      return legacyApi.finalizeVersion(resumeId, versionId);
    }
    const job = await appwriteWorkspace.finalizeResume(resumeId, versionId);
    const output = await waitForAgentJob<{
      version: ResumeVersion;
      review: ResumeReviewResult;
    }>(job);
    return output.version;
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
