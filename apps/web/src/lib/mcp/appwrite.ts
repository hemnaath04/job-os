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
import { Client, ID, Permission, Query, Role, TablesDB, type Models } from "node-appwrite";
import { appwriteUserIdForClerk } from "@/lib/appwrite/user-id";
import type { Application } from "@/lib/types";

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
  };
}

function tablesClient() {
  const { endpoint, projectId, apiKey } = config();
  const client = new Client().setEndpoint(endpoint).setProject(projectId).setKey(apiKey);
  return new TablesDB(client);
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
    permissions: [
      Permission.read(Role.user(appwriteUserId)),
      Permission.update(Role.user(appwriteUserId)),
      Permission.delete(Role.user(appwriteUserId)),
    ],
  });
  return fromRow(row);
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

export { ID };
