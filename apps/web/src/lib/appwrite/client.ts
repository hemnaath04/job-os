"use client";

import {
  Account,
  Client,
  Functions,
  Query,
  Storage,
  TablesDB,
  type Models,
} from "appwrite";
import type { Application, AppStatus } from "@/lib/types";
import { requirePublicAppwriteConfig } from "./config";

interface ApplicationCardRow extends Models.Row {
  owner_id: string;
  status: AppStatus;
  archived: boolean;
  snapshot: string;
  source_updated_at: string;
  migrated_at: string;
}

let services:
  | {
      account: Account;
      functions: Functions;
      storage: Storage;
      tables: TablesDB;
    }
  | undefined;
let sessionPromise: Promise<void> | undefined;
let currentUserId: string | undefined;

function getServices() {
  if (services) return services;

  const config = requirePublicAppwriteConfig();
  const client = new Client()
    .setEndpoint(config.endpoint)
    .setProject(config.projectId);

  services = {
    account: new Account(client),
    functions: new Functions(client),
    storage: new Storage(client),
    tables: new TablesDB(client),
  };
  return services;
}

/**
 * Whose Appwrite session are we allowed to be holding right now.
 *
 * Asked on every session establishment rather than cached across them: the
 * answer changes the moment a different person signs in, and that change is
 * precisely what this guards against.
 */
async function expectedAppwriteUserId(): Promise<string> {
  const response = await fetch("/api/appwrite/session", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Appwrite identity check failed (${response.status})`);
  }
  const { userId } = (await response.json()) as { userId: string };
  return userId;
}

/**
 * Bind this browser to the Appwrite session belonging to the signed-in user,
 * and to no one else's.
 *
 * The identity check is the load-bearing part. An Appwrite session outlives the
 * Clerk one: signing out of Clerk does not end it, and the SDK keeps it in an
 * app-origin localStorage fallback whenever the third-party cookie is dropped,
 * which is the normal case here (see `appwriteFileAuthHeaders` below). So an
 * `account.get()` that succeeds proves only that SOME session exists, not that
 * it is ours. Trusting it is how the next person to sign in on a shared browser
 * inherits the previous user's resumes, profile and applications, and has their
 * own writes land in that user's workspace. Verify the owner, and drop any
 * session that fails.
 */
export async function ensureAppwriteSession(): Promise<void> {
  if (sessionPromise) return sessionPromise;

  sessionPromise = (async () => {
    const { account } = getServices();
    const expected = await expectedAppwriteUserId();

    try {
      const user = await account.get();
      if (user.$id === expected) {
        currentUserId = user.$id;
        return;
      }
      // Somebody else's session. Not ours to keep or to read from.
      await account.deleteSession("current").catch(() => undefined);
    } catch {
      // No usable session yet, which is the ordinary first-load path.
    }

    const response = await fetch("/api/appwrite/session", {
      method: "POST",
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`Appwrite session bridge failed (${response.status})`);
    }
    const token = (await response.json()) as { userId: string; secret: string };
    await account.createSession(token);
    currentUserId = token.userId;
  })().catch((error) => {
    sessionPromise = undefined;
    throw error;
  });

  return sessionPromise;
}

/**
 * End the Appwrite session and forget everything derived from it.
 *
 * Called on sign-out so the session does not outlive the person who opened it.
 * `ensureAppwriteSession` would catch a leftover session on the next sign-in
 * anyway, but that is the backstop; this is the part that keeps a signed-out
 * user's data from sitting in the browser at all. Best effort by design: a
 * failed delete must never block sign-out, so the local state is cleared either
 * way and the identity check covers what is left.
 */
export async function clearAppwriteSession(): Promise<void> {
  try {
    await getServices().account.deleteSession("current");
  } catch {
    // Already gone, offline, or never established. Nothing to recover.
  } finally {
    sessionPromise = undefined;
    currentUserId = undefined;
  }
}

export function getAppwriteServices() {
  return getServices();
}

/**
 * Headers that authorize reading an Appwrite file URL with `fetch`.
 *
 * A file URL from the SDK is a plain link, so fetching it authenticates by the
 * Appwrite session cookie. That cookie is third-party here, because the API is
 * on another domain than the app, and Safari, Brave and increasingly Chrome
 * refuse to send those. The request then arrives unauthenticated and a file
 * permissioned to one user answers 404, which reads as a missing file rather
 * than a blocked one. A JWT carries the same identity in a header, which no
 * cookie policy can strip.
 *
 * Minted per call: a JWT lasts about 15 minutes, so caching one buys little and
 * risks handing out an expired token.
 */
export async function appwriteFileAuthHeaders(): Promise<Record<string, string>> {
  await ensureAppwriteSession();
  const config = requirePublicAppwriteConfig();
  const { jwt } = await getServices().account.createJWT();
  return {
    "X-Appwrite-Project": config.projectId,
    "X-Appwrite-JWT": jwt,
  };
}

export function getCurrentAppwriteUserId(): string {
  if (!currentUserId) throw new Error("Appwrite session has no user");
  return currentUserId;
}

function applicationFromRow(row: ApplicationCardRow): Application {
  const snapshot = JSON.parse(row.snapshot) as Application;
  return {
    ...snapshot,
    status: row.status,
    archived: row.archived,
    updated_at: row.source_updated_at,
  };
}

function searchableApplication(application: Application, query: string): boolean {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  return [
    application.job.title,
    application.job.company?.name,
    application.job.location,
  ].some((value) => value?.toLowerCase().includes(normalized));
}

export const appwritePipeline = {
  async listApplications(params?: {
    status?: AppStatus;
    q?: string;
  }): Promise<Application[]> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const queries = [
      Query.equal("archived", false),
      Query.orderDesc("source_updated_at"),
      Query.limit(500),
    ];
    if (params?.status) queries.push(Query.equal("status", params.status));

    const result = await getServices().tables.listRows<ApplicationCardRow>({
      databaseId: config.databaseId,
      tableId: config.applicationsTableId,
      queries,
      total: false,
      ttl: 0,
    });

    return result.rows
      .map(applicationFromRow)
      .filter((application) =>
        params?.q ? searchableApplication(application, params.q) : true,
      );
  },

  async patchApplication(
    id: string,
    patch: Partial<Application>,
  ): Promise<Application> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const tables = getServices().tables;
    const row = await tables.getRow<ApplicationCardRow>({
      databaseId: config.databaseId,
      tableId: config.applicationsTableId,
      rowId: id,
    });

    const now = new Date().toISOString();
    const updated: Application = {
      ...applicationFromRow(row),
      ...patch,
      updated_at: now,
    };

    const saved = await tables.updateRow<ApplicationCardRow>({
      databaseId: config.databaseId,
      tableId: config.applicationsTableId,
      rowId: id,
      data: {
        status: updated.status,
        archived: updated.archived,
        source_updated_at: now,
        snapshot: JSON.stringify(updated),
      },
    });

    return applicationFromRow(saved);
  },

  archiveApplication(id: string): Promise<Application> {
    return this.patchApplication(id, { archived: true });
  },

  async createApplicationCard(application: Application): Promise<Application> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    if (!currentUserId) throw new Error("Appwrite session has no user");
    const now = new Date().toISOString();

    const row = await getServices().tables.createRow<ApplicationCardRow>({
      databaseId: config.databaseId,
      tableId: config.applicationsTableId,
      rowId: application.id,
      data: {
        owner_id: currentUserId,
        status: application.status,
        archived: application.archived,
        snapshot: JSON.stringify(application),
        source_updated_at: application.updated_at,
        migrated_at: now,
      },
    });
    return applicationFromRow(row);
  },
};
