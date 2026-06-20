import type {
  Application,
  AppStatus,
  CalendarEntry,
  DiscoveryResult,
  DiscoverySearchRequest,
  Job,
  MeRead,
  ProfileFact,
  Resume,
  ResumeVersion,
  ResumeVersionSummary,
  SavedSearch,
  SmartSearchResponse,
  TailorResponse,
  UserSettings,
  UserSettingsPatch,
} from "./types";

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

export const api = {
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
  listVersions: (resumeId: string) =>
    request<ResumeVersionSummary[]>(`/resumes/${resumeId}/versions`),
  getVersion: (resumeId: string, versionId: string) =>
    request<ResumeVersion>(`/resumes/${resumeId}/versions/${versionId}`),
  approveVersion: (resumeId: string, versionId: string) =>
    request<ResumeVersion>(`/resumes/${resumeId}/versions/${versionId}/approve`, {
      method: "POST",
    }),
  downloadVersionUrl: (resumeId: string, versionId: string) =>
    `/api/backend/resumes/${resumeId}/versions/${versionId}/download`,

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
    request<DiscoveryResult[]>("/discovery/search", {
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
    request<DiscoveryResult[]>(`/discovery/saved/${id}/run`, { method: "POST" }),
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
