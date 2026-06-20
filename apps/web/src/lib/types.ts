// Hand-rolled mirror of apps/api Pydantic schemas.
// TODO: replace with auto-generated from OpenAPI once pnpm gen:types runs.

export type AppStatus =
  | "wishlist"
  | "ready_to_apply"
  | "applied"
  | "oa_received"
  | "interview_scheduled"
  | "offer"
  | "accepted"
  | "rejected"
  | "withdrawn"
  | "ghosted";

export const STATUS_LABELS: Record<AppStatus, string> = {
  wishlist: "Wishlist",
  ready_to_apply: "Ready",
  applied: "Applied",
  oa_received: "OA",
  interview_scheduled: "Interview",
  offer: "Offer",
  accepted: "Accepted",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  ghosted: "Ghosted",
};

export const KANBAN_STATUSES: AppStatus[] = [
  "wishlist",
  "ready_to_apply",
  "applied",
  "oa_received",
  "interview_scheduled",
  "offer",
];

export interface Company {
  id: string;
  name: string;
  domain?: string | null;
  industry?: string | null;
}

export interface Job {
  id: string;
  company?: Company | null;
  title: string;
  level?: string | null;
  function?: string | null;
  location?: string | null;
  remote?: string | null;
  source: string;
  source_url?: string | null;
  posted_at?: string | null;
  active: boolean;
  jd_parsed?: {
    required_skills?: string[];
    preferred_skills?: string[];
    technologies?: string[];
    keywords?: string[];
  } | null;
}

export interface FactBullet {
  id: string;
  text: string;
  target_role: string | null;
  metric_verified: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProfileFact {
  id: string;
  kind: "education" | "experience" | "project" | "skill" | "certification" |
        "publication" | "award" | "volunteering" | "leadership";
  title: string;
  org: string | null;
  start_date: string | null;
  end_date: string | null;
  location: string | null;
  payload: Record<string, unknown>;
  verified: boolean;
  source_url: string | null;
  bullets: FactBullet[];
  created_at: string;
  updated_at: string;
}

export interface Resume {
  id: string;
  name: string;
  base_role: string | null;
  is_master: boolean;
  created_at: string;
  updated_at: string;
}

export interface ResumeVersionSummary {
  id: string;
  resume_id: string;
  ats_score: string | null;
  approved_by_user: boolean;
  pdf_r2_key: string | null;
  docx_r2_key: string | null;
  spawned_from_job_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Application {
  id: string;
  job: Job;
  status: AppStatus;
  applied_at: string | null;
  recruiter_name: string | null;
  recruiter_email: string | null;
  notes: string | null;
  next_action_at: string | null;
  next_action_label: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
}
