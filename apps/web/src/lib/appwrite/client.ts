"use client";

import {
  Account,
  Client,
  Query,
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
    tables: new TablesDB(client),
  };
  return services;
}

async function ensureSession(): Promise<void> {
  if (sessionPromise) return sessionPromise;

  sessionPromise = (async () => {
    const { account } = getServices();
    try {
      const user = await account.get();
      currentUserId = user.$id;
      return;
    } catch {
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
    }
  })().catch((error) => {
    sessionPromise = undefined;
    throw error;
  });

  return sessionPromise;
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
    await ensureSession();
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
    await ensureSession();
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
    await ensureSession();
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
