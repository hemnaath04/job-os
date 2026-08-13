/**
 * Cover letters: types and the client that talks to them.
 *
 * A module of its own rather than more entries in `lib/api.ts`, and the reason
 * is what that file is: almost every export in it is a fork between an Appwrite
 * implementation and the legacy Postgres one, because the workspace moved and
 * the two backends have to coexist. Cover letters have no Appwrite side. They
 * are Postgres and FastAPI only, so every function here would be a branch with
 * one arm, and putting them in `api` would suggest a choice that does not exist.
 *
 * Everything goes through the same `/api/backend` proxy as the rest of the app,
 * so the Clerk token is injected server-side exactly as it is for resumes.
 */

/** One claim in the letter, and the verified bullet that proves it. */
export interface CoverLetterProvenanceEntry {
  /** Index into `document.paragraphs`, so the UI can point at the sentence. */
  paragraph: number;
  sentence: number;
  text: string;
  fact_bullet_id: string;
  fact_id: string;
}

/**
 * A sentence the backend would not print, and why.
 *
 * Shown rather than hidden. A user who can see that a claim was dropped for
 * inventing a metric learns something about their own profile; a silently
 * shorter letter teaches them nothing.
 */
export interface RefusedSentence {
  text: string;
  reason: string;
  fact_bullet_id?: string | null;
}

export interface CoverLetterGapQuestion {
  requirement: string;
  why_no_match: string;
  suggested_fact_ids?: string[];
}

export interface CoverLetterSender {
  name: string;
  email: string;
  phone: string;
  location: string;
  links: string[];
}

export interface CoverLetterDocument {
  sender: CoverLetterSender;
  date: string;
  company: string;
  role: string;
  recipient_name: string;
  greeting: string;
  subject: string;
  paragraphs: string[];
  signoff: string;
  word_count: number;
}

export type CoverLetterTone = "plain" | "warm" | "direct";

export interface CoverLetterVersionSummary {
  id: string;
  created_at: string;
  updated_at: string;
  cover_letter_id: string;
  spawned_from_job_id?: string | null;
  spawned_from_application_id?: string | null;
  parent_version_id?: string | null;
  status: string;
  tone: string;
  template_key?: string | null;
  word_count?: number | null;
  approved_by_user: boolean;
  revision_note?: string | null;
  finalized_at?: string | null;
  archived_at?: string | null;
}

export interface CoverLetterVersion extends CoverLetterVersionSummary {
  document: CoverLetterDocument;
  provenance: CoverLetterProvenanceEntry[];
  gap_questions: CoverLetterGapQuestion[];
  refused: RefusedSentence[];
  quality_flags: Record<string, string[]>;
  agent_note: string;
}

export interface CoverLetter {
  id: string;
  created_at: string;
  updated_at: string;
  name: string;
  job_id?: string | null;
  archived_at?: string | null;
}

const BASE = "/api/backend/cover-letters";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    // Read the body once, as text, for the same reason `lib/api.ts` does: a
    // failed res.json() still consumes the stream on its way to throwing, and a
    // second read then replaces the real error with "body already used".
    const body = await res.text();
    let detail: unknown = body;
    try {
      detail = (JSON.parse(body) as { detail?: unknown }).detail ?? body;
    } catch {
      /* not JSON, keep the raw text */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/**
 * Writing a letter is two Claude passes over a full profile, so it runs in
 * minutes rather than seconds. Capped just under the proxy route's own
 * maxDuration of 300s, so an overrun surfaces as this message instead of
 * whatever the platform returns when it tears a function down mid-flight.
 */
const GENERATE_TIMEOUT_MS = 290 * 1_000;

async function withTimeout<T>(work: Promise<T>, ms: number, message: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      work,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => reject(new Error(message)), ms);
      }),
    ]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export const coverLetters = {
  list: () => request<CoverLetter[]>(""),

  listVersions: (letterId: string) =>
    request<CoverLetterVersionSummary[]>(`/${letterId}/versions`),

  getVersion: (letterId: string, versionId: string) =>
    request<CoverLetterVersion>(`/${letterId}/versions/${versionId}`),

  generate: (body: {
    job_id: string;
    tone?: CoverLetterTone;
    template_key?: string | null;
    recipient_name?: string | null;
    parent_version_id?: string | null;
    revision_note?: string | null;
  }) =>
    withTimeout(
      request<CoverLetterVersion>("/generate", {
        method: "POST",
        body: JSON.stringify(body),
      }),
      GENERATE_TIMEOUT_MS,
      "Writing the letter ran out of time. The API container may be waking up, try again in a moment.",
    ),

  /**
   * Save a hand-edited letter as a new version.
   *
   * The edit is never rejected, but a provenance row survives only where the
   * sentence text is unchanged and its evidence is still verified, so an edited
   * claim comes back with nothing asserting that it is proved.
   */
  edit: (letterId: string, versionId: string, paragraphs: string[], note: string) =>
    request<CoverLetterVersion>(`/${letterId}/versions/${versionId}/edit`, {
      method: "POST",
      body: JSON.stringify({ paragraphs, note }),
    }),

  approve: (letterId: string, versionId: string) =>
    request<CoverLetterVersion>(`/${letterId}/versions/${versionId}/approve`, {
      method: "POST",
    }),

  archive: (letterId: string) => request<void>(`/${letterId}`, { method: "DELETE" }),

  downloadUrl: (letterId: string, versionId: string) =>
    `${BASE}/${letterId}/versions/${versionId}/download`,
};

/**
 * Turn a refusal reason into something a person can act on.
 *
 * The backend's reasons are machine-readable on purpose, since the repair pass
 * reads them too, and `unverified_number(40%)` is not a sentence. Unknown
 * reasons fall through to the raw code rather than to a vague catch-all, because
 * an unexplained code the user can search for beats a soothing lie.
 */
export function explainRefusal(reason: string): string {
  const [code, detail] = reason.split(/[()]/);
  switch (code) {
    case "unverified_number":
      return `Claimed a number (${detail}) that its evidence does not carry.`;
    case "unverified_technology":
      return `Named a technology (${detail}) that its evidence does not carry.`;
    case "unattributed_number":
      return `Used a number (${detail}) without pointing at any evidence.`;
    case "unattributed_technology":
      return `Named a technology (${detail}) without pointing at any evidence.`;
    case "unattributed_claim":
      return `Claimed past work ("${detail}") without pointing at any evidence.`;
    case "unknown_fact_bullet_id":
      return "Cited evidence that is not in your verified profile.";
    case "upgraded_status":
      return "Described provisional work as finished.";
    case "dropped_team_credit":
      return "Took sole credit for work your profile records as a team's.";
    case "banned_wording":
      return `Used wording this product does not send (${detail}).`;
    case "first_person_plural":
      return `Wrote "${detail}" in a letter about your own work.`;
    case "edited_claim_unverified":
      return "You edited this sentence, so nothing verified backs it any more.";
    default:
      return reason;
  }
}
