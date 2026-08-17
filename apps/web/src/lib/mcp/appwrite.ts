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
import { Client, ID, Permission, Query, Role, Storage, TablesDB, type Models } from "node-appwrite";
import { InputFile } from "node-appwrite/file";
import { appwriteUserIdForClerk } from "@/lib/appwrite/user-id";
import type { Application, Resume, ResumeVersion } from "@/lib/types";

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

export { ID };
