/**
 * Server-side Appwrite access for the MCP connector, for the one thing the
 * FastAPI/Postgres backend the rest of this route talks to cannot see:
 * production reads and writes Applications through Appwrite
 * (NEXT_PUBLIC_PIPELINE_BACKEND=appwrite), not Postgres. A tool that only
 * wrote to Postgres would succeed and be permanently invisible in the web
 * app — this is what actually happened with the first MCP-added job.
 *
 * Mirrors the browser's own dance in lib/appwrite/client.ts, but with an API
 * key instead of a browser session (that module is "use client" and depends
 * on cookies/localStorage a serverless function has none of), matching the
 * pattern /api/appwrite/session/route.ts already uses for the same reason.
 * Since an API key bypasses Appwrite's row-level permissions entirely, every
 * read here filters on owner_id explicitly — that filter is what the
 * session-based browser client gets for free from Appwrite's permission
 * system instead.
 */
import {
  Client,
  ExecutionMethod,
  Functions,
  ID,
  Permission,
  Query,
  Role,
  Storage,
  TablesDB,
  type Models,
} from "node-appwrite";
import { InputFile } from "node-appwrite/file";
import { appwriteUserIdForClerk } from "@/lib/appwrite/user-id";
import type {
  Application,
  FactBullet,
  ProfileFact,
  Resume,
  ResumeReviewResult,
  ResumeVersion,
} from "@/lib/types";

function config() {
  const endpoint = process.env.NEXT_PUBLIC_APPWRITE_ENDPOINT;
  const projectId = process.env.NEXT_PUBLIC_APPWRITE_PROJECT_ID;
  const apiKey = process.env.APPWRITE_API_KEY;
  if (!endpoint || !projectId || !apiKey) {
    throw new Error("Appwrite server environment is incomplete");
  }
  return {
    endpoint,
    projectId,
    apiKey,
    databaseId: process.env.NEXT_PUBLIC_APPWRITE_DATABASE_ID ?? "job-os",
    applicationsTableId:
      process.env.NEXT_PUBLIC_APPWRITE_APPLICATIONS_TABLE_ID ?? "application_cards",
    resumesTableId: process.env.NEXT_PUBLIC_APPWRITE_RESUMES_TABLE_ID ?? "resumes",
    resumeVersionsTableId:
      process.env.NEXT_PUBLIC_APPWRITE_RESUME_VERSIONS_TABLE_ID ?? "resume_versions",
    resumeFilesBucketId:
      process.env.NEXT_PUBLIC_APPWRITE_RESUME_FILES_BUCKET_ID ?? "resume_files",
    agentJobsTableId:
      process.env.NEXT_PUBLIC_APPWRITE_AGENT_JOBS_TABLE_ID ?? "agent_jobs",
    agentFunctionId:
      process.env.NEXT_PUBLIC_APPWRITE_AGENT_FUNCTION_ID ?? "job-os-agents",
    profileFactsTableId:
      process.env.NEXT_PUBLIC_APPWRITE_PROFILE_FACTS_TABLE_ID ?? "profile_facts",
    factBulletsTableId:
      process.env.NEXT_PUBLIC_APPWRITE_FACT_BULLETS_TABLE_ID ?? "fact_bullets",
  };
}

function client() {
  const { endpoint, projectId, apiKey } = config();
  return new Client().setEndpoint(endpoint).setProject(projectId).setKey(apiKey);
}

function tablesClient() {
  return new TablesDB(client());
}

function storageClient() {
  return new Storage(client());
}

function functionsClient() {
  return new Functions(client());
}

function ownerPermissions(appwriteUserId: string): string[] {
  return [
    Permission.read(Role.user(appwriteUserId)),
    Permission.update(Role.user(appwriteUserId)),
    Permission.delete(Role.user(appwriteUserId)),
  ];
}

async function rowExists(tableId: string, id: string): Promise<boolean> {
  const { databaseId } = config();
  try {
    await tablesClient().getRow({ databaseId, tableId, rowId: id });
    return true;
  } catch {
    return false;
  }
}

export function resolveAppwriteUserId(clerkUserId: string): string {
  return appwriteUserIdForClerk(clerkUserId);
}

interface ApplicationCardRow extends Models.Row {
  owner_id: string;
  status: string;
  archived: boolean;
  snapshot: string;
  source_updated_at: string;
  migrated_at: string;
}

function fromRow(row: ApplicationCardRow): Application {
  const snapshot = JSON.parse(row.snapshot) as Application;
  return {
    ...snapshot,
    status: row.status as Application["status"],
    archived: row.archived,
    updated_at: row.source_updated_at,
  };
}

export async function listApplicationCards(
  appwriteUserId: string,
  opts?: { status?: string; archived?: boolean },
): Promise<Application[]> {
  const { databaseId, applicationsTableId } = config();
  const queries = [
    Query.equal("owner_id", appwriteUserId),
    Query.equal("archived", opts?.archived ?? false),
    Query.orderDesc("source_updated_at"),
    Query.limit(500),
  ];
  if (opts?.status) queries.push(Query.equal("status", opts.status));

  const result = await tablesClient().listRows<ApplicationCardRow>({
    databaseId,
    tableId: applicationsTableId,
    queries,
  });
  return result.rows.map(fromRow);
}

export async function getApplicationCard(
  appwriteUserId: string,
  id: string,
): Promise<Application> {
  const { databaseId, applicationsTableId } = config();
  const row = await tablesClient().getRow<ApplicationCardRow>({
    databaseId,
    tableId: applicationsTableId,
    rowId: id,
  });
  if (row.owner_id !== appwriteUserId) throw new Error("application not found");
  return fromRow(row);
}

/**
 * Mirrors api.ts's createApplication dual-write exactly: the FastAPI/Postgres
 * row (passed in as `application`, already created by the caller) stays the
 * durable record; this is the read-path mirror the browser actually shows.
 * Best-effort by the same reasoning as the frontend's own comment on this —
 * a failure here doesn't undo the Postgres row, it just needs replaying.
 *
 * Explicit `permissions` here is load-bearing, not decoration: the browser's
 * own createApplicationCard never sets one because Appwrite auto-grants the
 * creating session's user read/write on that row. An API-key client has no
 * session to inherit from, so a row created without this would write fine
 * and then never appear in the owner's own session-scoped listApplications —
 * silently, since the write itself reports success.
 */
export async function createApplicationCard(
  appwriteUserId: string,
  application: Application,
): Promise<Application> {
  const { databaseId, applicationsTableId } = config();
  const now = new Date().toISOString();
  const row = await tablesClient().createRow<ApplicationCardRow>({
    databaseId,
    tableId: applicationsTableId,
    rowId: application.id,
    data: {
      owner_id: appwriteUserId,
      status: application.status,
      archived: application.archived,
      snapshot: JSON.stringify(application),
      source_updated_at: application.updated_at,
      migrated_at: now,
    },
    permissions: ownerPermissions(appwriteUserId),
  });
  return fromRow(row);
}

export async function applicationCardExists(id: string): Promise<boolean> {
  const { applicationsTableId } = config();
  return rowExists(applicationsTableId, id);
}

/**
 * Status changes and archiving write only here once Appwrite is the pipeline
 * backend — matching api.ts's patchApplication/archiveApplication, which do
 * not touch Postgres at all in that mode. Postgres's own status column goes
 * stale after this; that mismatch already exists in production today and
 * isn't something to paper over from an MCP tool.
 */
export async function patchApplicationCard(
  appwriteUserId: string,
  id: string,
  patch: Partial<Pick<Application, "status" | "archived" | "notes">>,
): Promise<Application> {
  const { databaseId, applicationsTableId } = config();
  const tables = tablesClient();
  const row = await tables.getRow<ApplicationCardRow>({
    databaseId,
    tableId: applicationsTableId,
    rowId: id,
  });
  if (row.owner_id !== appwriteUserId) throw new Error("application not found");

  const now = new Date().toISOString();
  const updated: Application = { ...fromRow(row), ...patch, updated_at: now };
  const saved = await tables.updateRow<ApplicationCardRow>({
    databaseId,
    tableId: applicationsTableId,
    rowId: id,
    data: {
      status: updated.status,
      archived: updated.archived,
      source_updated_at: now,
      snapshot: JSON.stringify(updated),
    },
  });
  return fromRow(saved);
}

/**
 * The resume_versions.status column is a strict Appwrite enum of
 * draft/reviewed/final. The Postgres vocabulary is wider ("needs_changes"),
 * and writing a value outside the enum rejects the whole row. Mirrors
 * versionStatusColumn in lib/appwrite/workspace.ts: the snapshot JSON below
 * carries the precise status, which is what the UI actually reads.
 */
const VERSION_STATUS_COLUMN_VALUES = new Set(["draft", "reviewed", "final"]);
function versionStatusColumn(status: string): string {
  return VERSION_STATUS_COLUMN_VALUES.has(status) ? status : "draft";
}

export async function resumeCardExists(id: string): Promise<boolean> {
  const { resumesTableId } = config();
  return rowExists(resumesTableId, id);
}

export async function resumeVersionCardExists(id: string): Promise<boolean> {
  const { resumeVersionsTableId } = config();
  return rowExists(resumeVersionsTableId, id);
}

/**
 * Mirrors the Resume *container* only — the data identity ("AI / Backend
 * SWE"), not any file. Reuses the Postgres id as the Appwrite row id, same
 * as createApplicationCard, so a later call for the same resume is a clean
 * existence check rather than a guess at whether it was already mirrored.
 */
export async function mirrorResumeCard(
  appwriteUserId: string,
  resume: Resume,
): Promise<void> {
  const { databaseId, resumesTableId } = config();
  await tablesClient().createRow({
    databaseId,
    tableId: resumesTableId,
    rowId: resume.id,
    data: {
      owner_id: appwriteUserId,
      name: resume.name,
      is_master: resume.is_master,
      archived: false,
      source_updated_at: resume.updated_at,
      snapshot: JSON.stringify(resume),
    },
    permissions: ownerPermissions(appwriteUserId),
  });
}

interface ResumeCardRow extends Models.Row {
  owner_id: string;
  name: string;
  is_master: boolean;
  archived: boolean;
  source_updated_at: string;
  snapshot: string;
}

/**
 * Every non-archived resume this user has, straight from Appwrite. Needed
 * for library cleanup: many resumes here (bulk imports, browser-only
 * tailoring) were never mirrored from Postgres and have no Postgres id at
 * all, so there is no way to enumerate or archive them through the backend
 * — this is the only place that can see them.
 */
export async function listResumeCards(appwriteUserId: string): Promise<Resume[]> {
  const { databaseId, resumesTableId } = config();
  const result = await tablesClient().listRows<ResumeCardRow>({
    databaseId,
    tableId: resumesTableId,
    queries: [
      Query.equal("owner_id", appwriteUserId),
      Query.equal("archived", false),
      Query.orderDesc("source_updated_at"),
      Query.limit(500),
    ],
  });
  return result.rows.map((row) => JSON.parse(row.snapshot) as Resume);
}

/**
 * Archives a resume directly in Appwrite — the only path available for a
 * resume that was never mirrored from Postgres (see listResumeCards). Master
 * is protected here too, mirroring the backend's own delete_resume guard.
 */
export async function archiveResumeCard(appwriteUserId: string, resumeId: string): Promise<void> {
  const { databaseId, resumesTableId } = config();
  const tables = tablesClient();
  const row = await tables.getRow<ResumeCardRow>({
    databaseId,
    tableId: resumesTableId,
    rowId: resumeId,
  });
  if (row.owner_id !== appwriteUserId) throw new Error("resume not found");
  if (row.is_master) throw new Error("The protected master cannot be archived.");
  const now = new Date().toISOString();
  const snapshot = { ...JSON.parse(row.snapshot), archived_at: now, updated_at: now };
  await tables.updateRow({
    databaseId,
    tableId: resumesTableId,
    rowId: resumeId,
    data: {
      archived: true,
      source_updated_at: now,
      snapshot: JSON.stringify(snapshot),
    },
  });
}

/**
 * Refreshes an already-mirrored resume card's snapshot from its current
 * Postgres state. Needed because a direct Postgres correction (a backfill,
 * a schema field added after the row was first mirrored) never reaches the
 * frozen Appwrite snapshot on its own -- only a write through this module
 * updates it, and mirrorResumeCard only creates, so a repeat call 409s
 * instead of refreshing anything.
 */
export async function resyncResumeCard(appwriteUserId: string, resume: Resume): Promise<void> {
  const { databaseId, resumesTableId } = config();
  const tables = tablesClient();
  const row = await tables.getRow({ databaseId, tableId: resumesTableId, rowId: resume.id });
  if (row.owner_id !== appwriteUserId) throw new Error("resume not found");
  await tables.updateRow({
    databaseId,
    tableId: resumesTableId,
    rowId: resume.id,
    data: {
      name: resume.name,
      is_master: resume.is_master,
      source_updated_at: resume.updated_at,
      snapshot: JSON.stringify(resume),
    },
  });
}

/**
 * Mirrors one resume version, including its PDF.
 *
 * The Appwrite Resume Studio never reads pdf_r2_key (R2 is only reachable
 * from the FastAPI/Python side); its download button resolves a
 * `pdf_file_id` in Appwrite Storage. So a metadata-only mirror would show
 * the version card with a dead download button — the PDF bytes have to be
 * copied into Appwrite Storage too, not just referenced. Caller supplies the
 * already-fetched bytes (from the FastAPI download endpoint, which is the
 * one thing on this side of the fence that can reach R2).
 */
export async function mirrorResumeVersionCard(
  appwriteUserId: string,
  version: ResumeVersion,
  pdf: { bytes: Uint8Array; filename: string } | null,
): Promise<void> {
  const { databaseId, resumeVersionsTableId, resumeFilesBucketId } = config();
  const permissions = ownerPermissions(appwriteUserId);

  let pdfFileId: string | undefined;
  if (pdf) {
    pdfFileId = ID.unique();
    await storageClient().createFile({
      bucketId: resumeFilesBucketId,
      fileId: pdfFileId,
      file: InputFile.fromBuffer(pdf.bytes, pdf.filename),
      permissions,
    });
  }

  const snapshot = pdfFileId ? { ...version, pdf_file_id: pdfFileId } : version;

  await tablesClient().createRow({
    databaseId,
    tableId: resumeVersionsTableId,
    rowId: version.id,
    data: {
      owner_id: appwriteUserId,
      resume_id: version.resume_id,
      status: versionStatusColumn(version.status),
      archived: false,
      source_updated_at: version.updated_at,
      snapshot: JSON.stringify(snapshot),
    },
    permissions,
  });
}

/**
 * Repoints an already-mirrored version at a different resume, in place.
 * Used when a version was mirrored under the wrong resume entirely (e.g.
 * several company-tailored uploads reused one generic container instead of
 * getting their own) — the row and its PDF file stay put, only resume_id and
 * the embedded snapshot change.
 */
export async function retargetResumeVersionCard(
  appwriteUserId: string,
  versionId: string,
  newResumeId: string,
): Promise<void> {
  const { databaseId, resumeVersionsTableId } = config();
  const tables = tablesClient();
  const row = await tables.getRow({
    databaseId,
    tableId: resumeVersionsTableId,
    rowId: versionId,
  });
  if (row.owner_id !== appwriteUserId) throw new Error("resume version not found");
  const snapshot = { ...JSON.parse(row.snapshot), resume_id: newResumeId };
  await tables.updateRow({
    databaseId,
    tableId: resumeVersionsTableId,
    rowId: versionId,
    data: {
      resume_id: newResumeId,
      snapshot: JSON.stringify(snapshot),
    },
  });
}

/**
 * The Appwrite tailor agent, invoked the same way the browser does it
 * (lib/appwrite/workspace.ts's createAgentJob) but from an API key instead of
 * a session — the one thing an MCP call has none of. This is what lets an
 * agent actually build a resume for a job through MCP rather than only
 * managing resumes that already exist. Multiple calls run as independent
 * agent jobs, each polled by its own id, so several builds genuinely run at
 * once instead of one MCP call blocking behind another.
 */
export type AgentJobStatus = "queued" | "running" | "succeeded" | "failed";

export interface AgentJobProgress {
  stage: string;
  pct: number;
  updated_at: string;
  step?: string | null;
  detail?: string | null;
}

export interface AgentJobSnapshot<T = unknown> {
  id: string;
  kind: "resume_tailor";
  status: AgentJobStatus;
  input: Record<string, unknown>;
  output: T | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  progress?: AgentJobProgress | null;
}

interface AgentJobRow extends Models.Row {
  owner_id: string;
  kind: string;
  status: string;
  snapshot: string;
}

/**
 * Dispatches the tailor agent for one resume against one job posting, and
 * returns immediately with the agent job's id. The draft it produces lands as
 * a new resume version, polled with getResumeTailorJobStatus below — mirrors
 * the browser's tailorResume + getAgentJob split exactly, since that split is
 * what already lets the web app poll without blocking.
 */
export async function startResumeTailorJob(
  appwriteUserId: string,
  resumeId: string,
  jobPostingId: string,
  jdParsed: Record<string, unknown>,
  jdClean: string,
): Promise<{ id: string }> {
  const { databaseId, resumesTableId, agentJobsTableId, agentFunctionId } = config();
  const tables = tablesClient();

  const resumeRow = await tables.getRow({
    databaseId,
    tableId: resumesTableId,
    rowId: resumeId,
  });
  if (resumeRow.owner_id !== appwriteUserId) {
    throw new Error("resume not found");
  }

  const id = ID.unique();
  const createdAt = new Date().toISOString();
  const input = {
    resume_id: resumeId,
    spawned_from_job_id: jobPostingId,
    jd_parsed: jdParsed,
    jd_clean: jdClean,
  };
  const snapshot: AgentJobSnapshot = {
    id,
    kind: "resume_tailor",
    status: "queued",
    input,
    output: null,
    error: null,
    created_at: createdAt,
    updated_at: createdAt,
  };

  await tables.createRow({
    databaseId,
    tableId: agentJobsTableId,
    rowId: id,
    data: {
      owner_id: appwriteUserId,
      kind: "resume_tailor",
      status: "queued",
      source_updated_at: createdAt,
      snapshot: JSON.stringify(snapshot),
    },
    permissions: ownerPermissions(appwriteUserId),
  });

  try {
    await functionsClient().createExecution({
      functionId: agentFunctionId,
      body: JSON.stringify({ job_id: id, ...input }),
      async: true,
      xpath: "/resume/tailor",
      method: ExecutionMethod.POST,
    });
  } catch (error) {
    const failedAt = new Date().toISOString();
    const failed: AgentJobSnapshot = {
      ...snapshot,
      status: "failed",
      error: error instanceof Error ? error.message : "Could not queue agent",
      updated_at: failedAt,
    };
    await tables.updateRow({
      databaseId,
      tableId: agentJobsTableId,
      rowId: id,
      data: {
        status: "failed",
        source_updated_at: failedAt,
        snapshot: JSON.stringify(failed),
      },
    });
    throw error;
  }

  return { id };
}

/**
 * Reads one tailor agent job's current state, the same row the Appwrite
 * Function itself updates as it runs. Owner-checked explicitly since an API
 * key has no session-based row permissions to fall back on.
 */
export async function getResumeTailorJobStatus(
  appwriteUserId: string,
  jobId: string,
): Promise<AgentJobSnapshot> {
  const { databaseId, agentJobsTableId } = config();
  const row = await tablesClient().getRow<AgentJobRow>({
    databaseId,
    tableId: agentJobsTableId,
    rowId: jobId,
  });
  if (row.owner_id !== appwriteUserId) {
    throw new Error("tailor job not found");
  }
  return JSON.parse(row.snapshot) as AgentJobSnapshot;
}

interface SnapshotOnlyRow extends Models.Row {
  owner_id: string;
  snapshot: string;
}

interface FactBulletRow extends SnapshotOnlyRow {
  fact_id: string;
}

/**
 * Verified career facts straight from Appwrite, the store the browser
 * actually reads and writes (NEXT_PUBLIC_WORKSPACE_BACKEND=appwrite) --
 * the FastAPI /profile/facts endpoint this MCP tool called before hits
 * Postgres instead, which is empty for every account whose facts were ever
 * entered through the web app. Same root cause, same fix, as the resume
 * mirroring in this file: an API key has no session to fall back on for
 * row-level permissions, so owner_id is filtered explicitly.
 */
export async function listProfileFacts(
  appwriteUserId: string,
  kind?: string,
): Promise<ProfileFact[]> {
  const { databaseId, profileFactsTableId, factBulletsTableId } = config();
  const tables = tablesClient();
  const factsResult = await tables.listRows<SnapshotOnlyRow>({
    databaseId,
    tableId: profileFactsTableId,
    queries: [
      Query.equal("owner_id", appwriteUserId),
      Query.equal("archived", false),
      Query.orderDesc("source_updated_at"),
      Query.limit(500),
    ],
  });
  const facts = factsResult.rows
    .map((row) => JSON.parse(row.snapshot) as ProfileFact)
    .filter((fact) => !kind || fact.kind === kind);
  if (!facts.length) return [];

  const bullets: (FactBullet & { fact_id: string })[] = [];
  for (let start = 0; start < facts.length; start += 100) {
    const ids = facts.slice(start, start + 100).map((fact) => fact.id);
    const result = await tables.listRows<FactBulletRow>({
      databaseId,
      tableId: factBulletsTableId,
      queries: [
        Query.equal("owner_id", appwriteUserId),
        Query.equal("fact_id", ids),
        Query.orderAsc("source_updated_at"),
        Query.limit(500),
      ],
    });
    bullets.push(
      ...result.rows.map((row) => ({
        ...(JSON.parse(row.snapshot) as FactBullet),
        fact_id: row.fact_id,
      })),
    );
  }
  const byFact = new Map<string, FactBullet[]>();
  for (const bullet of bullets) {
    const group = byFact.get(bullet.fact_id) ?? [];
    group.push(bullet);
    byFact.set(bullet.fact_id, group);
  }
  return facts.map((fact) => ({ ...fact, bullets: byFact.get(fact.id) ?? [] }));
}

/**
 * Mirrors appwriteWorkspace.createFact -- same shape, same two-table write
 * (the fact row, then one row per bullet), but from an API key instead of a
 * session, so permissions are granted explicitly the same way every other
 * write in this file does.
 */
export async function createProfileFact(
  appwriteUserId: string,
  input: {
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
  },
): Promise<ProfileFact> {
  const { databaseId, profileFactsTableId, factBulletsTableId } = config();
  const tables = tablesClient();
  const timestamp = new Date().toISOString();
  const permissions = ownerPermissions(appwriteUserId);

  const fact: ProfileFact = {
    id: ID.unique(),
    kind: input.kind as ProfileFact["kind"],
    title: input.title,
    org: input.org ?? null,
    start_date: input.start_date ?? null,
    end_date: input.end_date ?? null,
    location: input.location ?? null,
    payload: input.payload ?? {},
    verified: input.verified ?? false,
    source_url: input.source_url ?? null,
    bullets: [],
    created_at: timestamp,
    updated_at: timestamp,
  };

  await tables.createRow({
    databaseId,
    tableId: profileFactsTableId,
    rowId: fact.id,
    data: {
      owner_id: appwriteUserId,
      verified: fact.verified,
      archived: false,
      source_updated_at: timestamp,
      snapshot: JSON.stringify(fact),
    },
    permissions,
  });

  for (const inputBullet of input.bullets ?? []) {
    const bullet: FactBullet & { fact_id: string } = {
      id: ID.unique(),
      fact_id: fact.id,
      text: inputBullet.text,
      target_role: inputBullet.target_role ?? null,
      metric_verified: inputBullet.metric_verified ?? false,
      created_at: timestamp,
      updated_at: timestamp,
    };
    await tables.createRow({
      databaseId,
      tableId: factBulletsTableId,
      rowId: bullet.id,
      data: {
        owner_id: appwriteUserId,
        fact_id: fact.id,
        source_updated_at: timestamp,
        snapshot: JSON.stringify(bullet),
      },
      permissions,
    });
    fact.bullets.push(bullet);
  }

  return fact;
}

/** Mirrors appwriteWorkspace.archiveFact. Archives, never deletes. */
export async function archiveProfileFact(appwriteUserId: string, factId: string): Promise<void> {
  const { databaseId, profileFactsTableId } = config();
  const tables = tablesClient();
  const row = await tables.getRow<SnapshotOnlyRow>({
    databaseId,
    tableId: profileFactsTableId,
    rowId: factId,
  });
  if (row.owner_id !== appwriteUserId) throw new Error("profile fact not found");
  const timestamp = new Date().toISOString();
  const fact = { ...(JSON.parse(row.snapshot) as ProfileFact), updated_at: timestamp };
  await tables.updateRow({
    databaseId,
    tableId: profileFactsTableId,
    rowId: factId,
    data: {
      archived: true,
      source_updated_at: timestamp,
      snapshot: JSON.stringify(fact),
    },
  });
}

/**
 * Reads one resume version's full snapshot (including json_resume), owner
 * checked. Needed before dispatching a finalize job: the FastAPI
 * render-review endpoint is stateless and has no session of its own, so the
 * caller has to hand it the document.
 */
export async function getResumeVersionSnapshot(
  appwriteUserId: string,
  versionId: string,
): Promise<ResumeVersion> {
  const { databaseId, resumeVersionsTableId } = config();
  const row = await tablesClient().getRow<SnapshotOnlyRow>({
    databaseId,
    tableId: resumeVersionsTableId,
    rowId: versionId,
  });
  if (row.owner_id !== appwriteUserId) throw new Error("resume version not found");
  return JSON.parse(row.snapshot) as ResumeVersion;
}

/**
 * Reads a finalized version's rendered PDF straight out of Appwrite Storage,
 * for handing back to whoever asked for the download rather than mirroring it
 * somewhere else. Returns null (not a thrown error) when the version has no
 * `pdf_file_id` yet -- true of every draft start_resume_tailor produces and
 * of a reviewed-but-not-passed version -- since that is an ordinary, expected
 * state the caller should explain to a person, not treat as a failure.
 */
export async function downloadResumeVersionFile(
  appwriteUserId: string,
  versionId: string,
): Promise<{ version: ResumeVersion; bytes: Buffer } | null> {
  const version = await getResumeVersionSnapshot(appwriteUserId, versionId);
  const pdfFileId = (version as ResumeVersion & { pdf_file_id?: string }).pdf_file_id;
  if (!pdfFileId) return null;
  const { resumeFilesBucketId } = config();
  const raw = await storageClient().getFileDownload({
    bucketId: resumeFilesBucketId,
    fileId: pdfFileId,
  });
  return { version, bytes: Buffer.from(raw) };
}

/**
 * Persists a completed render-review result against a version, and finalizes
 * it when the review passed (or the caller forces it) -- the same sequence
 * the browser's finalizeVersion runs as three separate Appwrite writes
 * (attachReview, then markFinalized), collapsed into one so an MCP caller
 * gets a single answer once the backend job is done rather than needing a
 * third round trip.
 *
 * Called at most once per finished backend job: the render-review status
 * endpoint (apps/api's get_render_review_job) deletes a job from its
 * in-memory store the moment it is read as "done" or "error", so a second
 * poll with the same job_id 404s there before this function is ever reached
 * again. That is the only guard against double-uploading the PDF, and it is
 * enough -- there is no path back into this function for a job already
 * consumed.
 */
export async function attachReviewAndMaybeFinalize(
  appwriteUserId: string,
  versionId: string,
  result: { review: ResumeReviewResult; latex_source: string; pdf_base64: string },
  force: boolean,
): Promise<{ status: "blocked" | "finalized"; version: ResumeVersion }> {
  const { databaseId, resumeVersionsTableId, resumeFilesBucketId } = config();
  const tables = tablesClient();
  const permissions = ownerPermissions(appwriteUserId);

  const row = await tables.getRow<SnapshotOnlyRow>({
    databaseId,
    tableId: resumeVersionsTableId,
    rowId: versionId,
  });
  if (row.owner_id !== appwriteUserId) throw new Error("resume version not found");

  const pdfFileId = ID.unique();
  await storageClient().createFile({
    bucketId: resumeFilesBucketId,
    fileId: pdfFileId,
    file: InputFile.fromBuffer(Buffer.from(result.pdf_base64, "base64"), `${versionId}.pdf`),
    permissions,
  });

  const reviewedAt = new Date().toISOString();
  const passed = result.review.passed;
  const reviewedStatus = passed ? "reviewed" : "needs_changes";
  let version: ResumeVersion = {
    ...(JSON.parse(row.snapshot) as ResumeVersion),
    status: reviewedStatus,
    review_score: result.review.score,
    review_report: result.review,
    latex_source: result.latex_source,
    updated_at: reviewedAt,
  };
  (version as ResumeVersion & { pdf_file_id?: string }).pdf_file_id = pdfFileId;

  await tables.updateRow({
    databaseId,
    tableId: resumeVersionsTableId,
    rowId: versionId,
    data: {
      status: versionStatusColumn(reviewedStatus),
      source_updated_at: reviewedAt,
      snapshot: JSON.stringify(version),
    },
  });

  if (!passed && !force) {
    return { status: "blocked", version };
  }

  const finalizedAt = new Date().toISOString();
  version = {
    ...version,
    status: "final",
    approved_by_user: true,
    finalized_at: finalizedAt,
    updated_at: finalizedAt,
  };
  await tables.updateRow({
    databaseId,
    tableId: resumeVersionsTableId,
    rowId: versionId,
    data: {
      status: versionStatusColumn("final"),
      source_updated_at: finalizedAt,
      snapshot: JSON.stringify(version),
    },
  });
  return { status: "finalized", version };
}

export { ID };
