"use client";

import {
  ExecutionMethod,
  ID,
  Query,
  type Models,
} from "appwrite";
import type {
  JsonResume,
  Resume,
  ResumeVersion,
  ResumeVersionSummary,
  RevisionMessage,
} from "@/lib/types";
import {
  ensureAppwriteSession,
  getAppwriteServices,
  getCurrentAppwriteUserId,
} from "./client";
import { requirePublicAppwriteConfig } from "./config";

interface SnapshotRow extends Models.Row {
  owner_id: string;
  source_updated_at: string;
  snapshot: string;
}

interface ResumeRow extends SnapshotRow {
  name: string;
  is_master: boolean;
  archived: boolean;
}

interface VersionRow extends SnapshotRow {
  resume_id: string;
  status: string;
  archived: boolean;
}

interface MessageRow extends SnapshotRow {
  resume_id: string;
  version_id: string;
}

interface AgentJobRow extends SnapshotRow {
  kind: AgentJobKind;
  status: AgentJobStatus;
}

export type AgentJobKind =
  | "resume_import"
  | "resume_revision"
  | "resume_review"
  | "resume_finalize"
  | "resume_tailor"
  | "profile_extract"
  | "job_parse"
  | "job_discovery";

export type AgentJobStatus = "queued" | "running" | "succeeded" | "failed";

export interface AgentJob<T = unknown> {
  id: string;
  kind: AgentJobKind;
  status: AgentJobStatus;
  input: Record<string, unknown>;
  output: T | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

function parseSnapshot<T>(row: SnapshotRow): T {
  return JSON.parse(row.snapshot) as T;
}

function now(): string {
  return new Date().toISOString();
}

async function createAgentJob<TInput extends Record<string, unknown>>(
  kind: AgentJobKind,
  path: string,
  input: TInput,
): Promise<AgentJob> {
  await ensureAppwriteSession();
  const config = requirePublicAppwriteConfig();
  const { tables, functions } = getAppwriteServices();
  const ownerId = getCurrentAppwriteUserId();
  const createdAt = now();
  const id = ID.unique();
  const job: AgentJob = {
    id,
    kind,
    status: "queued",
    input,
    output: null,
    error: null,
    created_at: createdAt,
    updated_at: createdAt,
  };

  await tables.createRow<AgentJobRow>({
    databaseId: config.databaseId,
    tableId: config.agentJobsTableId,
    rowId: id,
    data: {
      owner_id: ownerId,
      kind,
      status: "queued",
      source_updated_at: createdAt,
      snapshot: JSON.stringify(job),
    },
  });

  try {
    await functions.createExecution({
      functionId: config.agentFunctionId,
      body: JSON.stringify({ job_id: id, ...input }),
      async: true,
      xpath: path,
      method: ExecutionMethod.POST,
    });
  } catch (error) {
    const failed = {
      ...job,
      status: "failed" as const,
      error: error instanceof Error ? error.message : "Could not queue agent",
      updated_at: now(),
    };
    await tables.updateRow<AgentJobRow>({
      databaseId: config.databaseId,
      tableId: config.agentJobsTableId,
      rowId: id,
      data: {
        status: "failed",
        source_updated_at: failed.updated_at,
        snapshot: JSON.stringify(failed),
      },
    });
    throw error;
  }
  return job;
}

export const appwriteWorkspace = {
  async listResumes(): Promise<Resume[]> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const result = await getAppwriteServices().tables.listRows<ResumeRow>({
      databaseId: config.databaseId,
      tableId: config.resumesTableId,
      queries: [
        Query.equal("archived", false),
        Query.orderDesc("source_updated_at"),
        Query.limit(500),
      ],
      total: false,
      ttl: 0,
    });
    return result.rows.map((row) => parseSnapshot<Resume>(row));
  },

  async createResume(input: {
    name: string;
    base_role?: string | null;
    is_master?: boolean;
  }): Promise<Resume> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const ownerId = getCurrentAppwriteUserId();
    const timestamp = now();
    const resume: Resume = {
      id: ID.unique(),
      name: input.name,
      base_role: input.base_role ?? null,
      is_master: input.is_master ?? false,
      source_kind: "appwrite",
      source_label: null,
      archived_at: null,
      created_at: timestamp,
      updated_at: timestamp,
    };
    await getAppwriteServices().tables.createRow<ResumeRow>({
      databaseId: config.databaseId,
      tableId: config.resumesTableId,
      rowId: resume.id,
      data: {
        owner_id: ownerId,
        name: resume.name,
        is_master: resume.is_master,
        archived: false,
        source_updated_at: timestamp,
        snapshot: JSON.stringify(resume),
      },
    });
    return resume;
  },

  async updateResume(
    resumeId: string,
    patch: { name?: string; base_role?: string | null },
  ): Promise<Resume> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const tables = getAppwriteServices().tables;
    const row = await tables.getRow<ResumeRow>({
      databaseId: config.databaseId,
      tableId: config.resumesTableId,
      rowId: resumeId,
    });
    const resume = {
      ...parseSnapshot<Resume>(row),
      ...patch,
      updated_at: now(),
    };
    await tables.updateRow<ResumeRow>({
      databaseId: config.databaseId,
      tableId: config.resumesTableId,
      rowId: resumeId,
      data: {
        name: resume.name,
        source_updated_at: resume.updated_at,
        snapshot: JSON.stringify(resume),
      },
    });
    return resume;
  },

  async archiveResume(resumeId: string): Promise<void> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const tables = getAppwriteServices().tables;
    const row = await tables.getRow<ResumeRow>({
      databaseId: config.databaseId,
      tableId: config.resumesTableId,
      rowId: resumeId,
    });
    const resume = {
      ...parseSnapshot<Resume>(row),
      archived_at: now(),
      updated_at: now(),
    };
    await tables.updateRow<ResumeRow>({
      databaseId: config.databaseId,
      tableId: config.resumesTableId,
      rowId: resumeId,
      data: {
        archived: true,
        source_updated_at: resume.updated_at,
        snapshot: JSON.stringify(resume),
      },
    });
  },

  async listVersions(resumeId: string): Promise<ResumeVersionSummary[]> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const result = await getAppwriteServices().tables.listRows<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      queries: [
        Query.equal("resume_id", resumeId),
        Query.equal("archived", false),
        Query.orderDesc("source_updated_at"),
        Query.limit(500),
      ],
      total: false,
      ttl: 0,
    });
    return result.rows.map((row) => parseSnapshot<ResumeVersion>(row));
  },

  async getVersion(versionId: string): Promise<ResumeVersion> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const row = await getAppwriteServices().tables.getRow<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      rowId: versionId,
    });
    return parseSnapshot<ResumeVersion>(row);
  },

  async editVersion(
    versionId: string,
    jsonResume: JsonResume,
    note: string,
  ): Promise<ResumeVersion> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const tables = getAppwriteServices().tables;
    const row = await tables.getRow<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      rowId: versionId,
    });
    const parent = parseSnapshot<ResumeVersion>(row);
    const timestamp = now();
    const version: ResumeVersion = {
      ...parent,
      id: ID.unique(),
      json_resume: jsonResume,
      status: "draft",
      approved_by_user: false,
      review_score: null,
      review_report: null,
      parent_version_id: parent.id,
      revision_note: note,
      finalized_at: null,
      archived_at: null,
      created_at: timestamp,
      updated_at: timestamp,
    };
    await tables.createRow<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      rowId: version.id,
      data: {
        owner_id: getCurrentAppwriteUserId(),
        resume_id: version.resume_id,
        status: "draft",
        archived: false,
        source_updated_at: timestamp,
        snapshot: JSON.stringify(version),
      },
    });
    return version;
  },

  async archiveVersion(versionId: string): Promise<void> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const tables = getAppwriteServices().tables;
    const row = await tables.getRow<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      rowId: versionId,
    });
    const version = {
      ...parseSnapshot<ResumeVersion>(row),
      archived_at: now(),
      updated_at: now(),
    };
    await tables.updateRow<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      rowId: versionId,
      data: {
        archived: true,
        source_updated_at: version.updated_at,
        snapshot: JSON.stringify(version),
      },
    });
  },

  async listMessages(versionId: string): Promise<RevisionMessage[]> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const result = await getAppwriteServices().tables.listRows<MessageRow>({
      databaseId: config.databaseId,
      tableId: config.resumeMessagesTableId,
      queries: [
        Query.equal("version_id", versionId),
        Query.orderAsc("source_updated_at"),
        Query.limit(500),
      ],
      total: false,
      ttl: 0,
    });
    return result.rows.map((row) => parseSnapshot<RevisionMessage>(row));
  },

  async uploadResume(file: File, isMaster: boolean): Promise<AgentJob> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const stored = await getAppwriteServices().storage.createFile({
      bucketId: config.resumeFilesBucketId,
      fileId: ID.unique(),
      file,
    });
    return createAgentJob("resume_import", "/resume/import", {
      file_id: stored.$id,
      filename: file.name,
      name: file.name.replace(/\.(pdf|docx|json)$/i, ""),
      is_master: isMaster,
    });
  },

  reviseResume(
    resumeId: string,
    versionId: string,
    message: string,
  ): Promise<AgentJob> {
    return createAgentJob("resume_revision", "/resume/revise", {
      resume_id: resumeId,
      version_id: versionId,
      message,
    });
  },

  reviewResume(resumeId: string, versionId: string): Promise<AgentJob> {
    return createAgentJob("resume_review", "/resume/review", {
      resume_id: resumeId,
      version_id: versionId,
    });
  },

  finalizeResume(resumeId: string, versionId: string): Promise<AgentJob> {
    return createAgentJob("resume_finalize", "/resume/finalize", {
      resume_id: resumeId,
      version_id: versionId,
    });
  },

  async getAgentJob<T = unknown>(jobId: string): Promise<AgentJob<T>> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const row = await getAppwriteServices().tables.getRow<AgentJobRow>({
      databaseId: config.databaseId,
      tableId: config.agentJobsTableId,
      rowId: jobId,
    });
    return parseSnapshot<AgentJob<T>>(row);
  },
};
