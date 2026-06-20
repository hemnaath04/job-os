import type {
  Application,
  AppStatus,
  Job,
  ProfileFact,
  Resume,
  ResumeVersionSummary,
} from "./types";

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
  downloadVersionUrl: (resumeId: string, versionId: string) =>
    `/api/backend/resumes/${resumeId}/versions/${versionId}/download`,
};
