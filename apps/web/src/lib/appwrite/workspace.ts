"use client";

import {
  ExecutionMethod,
  ID,
  Query,
  type Models,
} from "appwrite";
import type {
  FactBullet,
  JsonResume,
  ProfileFact,
  Resume,
  ResumeChatResponse,
  ResumeReviewResult,
  ResumeTemplate,
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

interface TemplateRow extends SnapshotRow {
  name: string;
  archived: boolean;
  html_source: string;
  css_source: string;
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

interface ProfileFactRow extends SnapshotRow {
  verified: boolean;
  archived: boolean;
}

interface FactBulletRow extends SnapshotRow {
  fact_id: string;
}

type StoredResumeVersion = ResumeVersion & {
  pdf_file_id?: string | null;
  source_file_id?: string | null;
};

type StoredFactBullet = FactBullet & {
  fact_id: string;
};

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

/**
 * Coarse progress written onto a running agent job's snapshot by the Appwrite
 * Function (currently only the tailor agent emits it). `pct` is a 0.0-1.0
 * fraction. Optional because older snapshots and other agents omit it.
 */
export interface AgentJobProgress {
  stage: string;
  pct: number;
  updated_at: string;
}

export interface AgentJob<T = unknown> {
  id: string;
  kind: AgentJobKind;
  status: AgentJobStatus;
  input: Record<string, unknown>;
  output: T | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  progress?: AgentJobProgress | null;
}

function parseSnapshot<T>(row: SnapshotRow): T {
  return JSON.parse(row.snapshot) as T;
}

function now(): string {
  return new Date().toISOString();
}

/**
 * The resume_versions.status column is an Appwrite enum of draft/reviewed/final.
 * The agent vocabulary is wider ("needs_changes" when a review did not clear the
 * draft), and writing a value outside the enum rejects the whole row. The
 * snapshot JSON carries the precise status, which is what the UI reads, so only
 * the column value is narrowed. Mirrors _status_column in the agent function.
 */
const VERSION_STATUS_COLUMN_VALUES = new Set(["draft", "reviewed", "final"]);

function versionStatusColumn(status: string): string {
  return VERSION_STATUS_COLUMN_VALUES.has(status) ? status : "draft";
}

/** Decode a base64 payload into bytes without pulling in a Buffer polyfill. */
function decodeBase64(value: string): ArrayBuffer {
  const binary = atob(value);
  const buffer = new ArrayBuffer(binary.length);
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return buffer;
}

const versionFileIds = new Map<string, string>();

function rememberVersionFile(version: ResumeVersion): ResumeVersion {
  const stored = version as StoredResumeVersion;
  if (stored.pdf_file_id) versionFileIds.set(version.id, stored.pdf_file_id);
  return version;
}

function ownerPermissions(ownerId: string): string[] {
  return [
    `read("user:${ownerId}")`,
    `update("user:${ownerId}")`,
    `delete("user:${ownerId}")`,
  ];
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
    job_posting_id?: string | null;
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
      job_posting_id: input.job_posting_id ?? null,
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

  async listTemplates(): Promise<ResumeTemplate[]> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const result = await getAppwriteServices().tables.listRows<TemplateRow>({
      databaseId: config.databaseId,
      tableId: config.templatesTableId,
      queries: [
        Query.equal("archived", false),
        Query.orderDesc("source_updated_at"),
        Query.limit(200),
      ],
      total: false,
      ttl: 0,
    });
    return result.rows.map((row) => ({
      ...parseSnapshot<ResumeTemplate>(row),
      // The row is the authority on the name, the snapshot can lag a rename.
      name: row.name,
    }));
  },

  /** The Jinja and CSS a template renders with. Fetched only when rendering. */
  async getTemplateSource(
    templateId: string,
  ): Promise<{ html_source: string; css_source: string }> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const row = await getAppwriteServices().tables.getRow<TemplateRow>({
      databaseId: config.databaseId,
      tableId: config.templatesTableId,
      rowId: templateId,
    });
    return { html_source: row.html_source, css_source: row.css_source };
  },

  /** Store a generated look. Creates a template row and nothing else. */
  async createTemplate(input: {
    name: string;
    html_source: string;
    css_source: string;
    notes?: string;
    preview_html?: string;
    created_from_resume_id?: string | null;
  }): Promise<ResumeTemplate> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const tables = getAppwriteServices().tables;
    const ownerId = getCurrentAppwriteUserId();
    const timestamp = now();

    // Names are how the user picks a look, so keep them unique rather than
    // silently ending up with three entries called Two Column Serif.
    const existing = await tables.listRows<TemplateRow>({
      databaseId: config.databaseId,
      tableId: config.templatesTableId,
      queries: [Query.equal("archived", false), Query.limit(200)],
      total: false,
      ttl: 0,
    });
    const taken = new Set(existing.rows.map((row) => row.name));
    let name = input.name.trim() || "Untitled look";
    for (let suffix = 2; taken.has(name); suffix += 1) {
      name = `${input.name.trim()} ${suffix}`;
    }

    const template: ResumeTemplate & { preview_html?: string } = {
      id: ID.unique(),
      name,
      description: input.notes?.trim() || null,
      created_from_resume_id: input.created_from_resume_id ?? null,
      source_file_id: null,
      preview_file_id: null,
      created_at: timestamp,
      updated_at: timestamp,
      preview_html: input.preview_html,
    };
    await tables.createRow<TemplateRow>({
      databaseId: config.databaseId,
      tableId: config.templatesTableId,
      rowId: template.id,
      data: {
        owner_id: ownerId,
        name,
        archived: false,
        html_source: input.html_source,
        css_source: input.css_source,
        source_updated_at: timestamp,
        snapshot: JSON.stringify(template),
      },
      permissions: ownerPermissions(ownerId),
    });
    return template;
  },

  /**
   * The original document a resume was imported from, if it still exists.
   *
   * Only some resumes have one, so callers must handle null rather than
   * inventing a look. Read from the versions, newest first, since that is where
   * the uploaded file is recorded.
   */
  async findResumeOriginalDocument(
    resumeId: string,
  ): Promise<{ fileId: string; filename: string } | null> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const result = await getAppwriteServices().tables.listRows<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      queries: [
        Query.equal("resume_id", resumeId),
        Query.equal("archived", false),
        Query.orderDesc("source_updated_at"),
        Query.limit(50),
      ],
      total: false,
      ttl: 0,
    });
    for (const row of result.rows) {
      const version = parseSnapshot<
        ResumeVersion & {
          pdf_file_id?: string | null;
          source_file_id?: string | null;
          source_filename?: string | null;
        }
      >(row);
      const fileId = version.source_file_id || version.pdf_file_id;
      if (fileId && version.source_filename) {
        return { fileId, filename: version.source_filename };
      }
    }
    return null;
  },

  /** Fetch a stored document so it can be sent for design extraction. */
  async downloadStoredFile(fileId: string, filename: string): Promise<File> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const url = getAppwriteServices().storage.getFileDownload({
      bucketId: config.resumeFilesBucketId,
      fileId,
    });
    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) {
      throw new Error(`Could not read the stored document (${response.status}).`);
    }
    return new File([await response.blob()], filename, {
      type: "application/pdf",
    });
  },

  /** Archive a template. Look-only, so no resume or version is affected. */
  async archiveTemplate(templateId: string): Promise<void> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const tables = getAppwriteServices().tables;
    const row = await tables.getRow<TemplateRow>({
      databaseId: config.databaseId,
      tableId: config.templatesTableId,
      rowId: templateId,
    });
    const template = {
      ...parseSnapshot<ResumeTemplate>(row),
      updated_at: now(),
    };
    await tables.updateRow<TemplateRow>({
      databaseId: config.databaseId,
      tableId: config.templatesTableId,
      rowId: templateId,
      data: {
        archived: true,
        source_updated_at: template.updated_at,
        snapshot: JSON.stringify(template),
      },
    });
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
    return result.rows.map((row) =>
      rememberVersionFile(parseSnapshot<ResumeVersion>(row)),
    );
  },

  async getVersion(versionId: string): Promise<ResumeVersion> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const row = await getAppwriteServices().tables.getRow<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      rowId: versionId,
    });
    return rememberVersionFile(parseSnapshot<ResumeVersion>(row));
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
    return rememberVersionFile(version);
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

  async approveVersion(versionId: string): Promise<ResumeVersion> {
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
      approved_by_user: true,
      updated_at: now(),
    };
    await tables.updateRow<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      rowId: versionId,
      data: {
        source_updated_at: version.updated_at,
        snapshot: JSON.stringify(version),
      },
    });
    return rememberVersionFile(version);
  },

  async applyRevisionProposal(
    resumeId: string,
    versionId: string,
    messageId: string,
  ): Promise<ResumeChatResponse> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const tables = getAppwriteServices().tables;
    const row = await tables.getRow<MessageRow>({
      databaseId: config.databaseId,
      tableId: config.resumeMessagesTableId,
      rowId: messageId,
    });
    const message = parseSnapshot<RevisionMessage>(row);
    if (message.resume_version_id !== versionId || !message.proposed_json_resume) {
      throw new Error("This revision proposal is unavailable.");
    }
    const version = await this.editVersion(
      versionId,
      message.proposed_json_resume,
      `AI revision: ${message.content.slice(0, 180)}`,
    );
    const updatedMessage = {
      ...message,
      applied: true,
      updated_at: now(),
    };
    await tables.updateRow<MessageRow>({
      databaseId: config.databaseId,
      tableId: config.resumeMessagesTableId,
      rowId: messageId,
      data: {
        source_updated_at: updatedMessage.updated_at,
        snapshot: JSON.stringify(updatedMessage),
      },
    });
    return {
      message: message.content,
      suggestions: message.suggestions,
      proposal_id: message.id,
      proposed_json_resume: message.proposed_json_resume,
      version: { ...version, resume_id: resumeId },
      review: null,
    };
  },

  async listFacts(kind?: string): Promise<ProfileFact[]> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const tables = getAppwriteServices().tables;
    const factQueries = [
      Query.equal("archived", false),
      Query.orderDesc("source_updated_at"),
      Query.limit(500),
    ];
    const factsResult = await tables.listRows<ProfileFactRow>({
      databaseId: config.databaseId,
      tableId: config.profileFactsTableId,
      queries: factQueries,
      total: false,
      ttl: 0,
    });
    const facts = factsResult.rows
      .map((row) => parseSnapshot<ProfileFact>(row))
      .filter((fact) => !kind || fact.kind === kind);
    if (!facts.length) return [];

    const bullets: StoredFactBullet[] = [];
    for (let start = 0; start < facts.length; start += 100) {
      const ids = facts.slice(start, start + 100).map((fact) => fact.id);
      const result = await tables.listRows<FactBulletRow>({
        databaseId: config.databaseId,
        tableId: config.factBulletsTableId,
        queries: [
          Query.equal("fact_id", ids),
          Query.orderAsc("source_updated_at"),
          Query.limit(500),
        ],
        total: false,
        ttl: 0,
      });
      bullets.push(
        ...result.rows.map((row) => ({
          ...parseSnapshot<FactBullet>(row),
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
    return facts.map((fact) => ({
      ...fact,
      bullets: byFact.get(fact.id) ?? [],
    }));
  },

  async createFact(input: {
    kind: string;
    title: string;
    org?: string | null;
    start_date?: string | null;
    end_date?: string | null;
    location?: string | null;
    payload?: Record<string, unknown>;
    verified?: boolean;
    source_url?: string | null;
    bullets?: {
      text: string;
      target_role?: string | null;
      metric_verified?: boolean;
    }[];
  }): Promise<ProfileFact> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const ownerId = getCurrentAppwriteUserId();
    const timestamp = now();
    const fact: ProfileFact = {
      id: ID.unique(),
      kind: input.kind as ProfileFact["kind"],
      title: input.title,
      org: input.org ?? null,
      start_date: input.start_date ?? null,
      end_date: input.end_date ?? null,
      location: input.location ?? null,
      payload: input.payload ?? {},
      verified: input.verified ?? true,
      source_url: input.source_url ?? null,
      bullets: [],
      created_at: timestamp,
      updated_at: timestamp,
    };
    const tables = getAppwriteServices().tables;
    const permissions = ownerPermissions(ownerId);
    await tables.createRow<ProfileFactRow>({
      databaseId: config.databaseId,
      tableId: config.profileFactsTableId,
      rowId: fact.id,
      data: {
        owner_id: ownerId,
        verified: fact.verified,
        archived: false,
        source_updated_at: timestamp,
        snapshot: JSON.stringify(fact),
      },
      permissions,
    });
    for (const inputBullet of input.bullets ?? []) {
      const bullet: StoredFactBullet = {
        id: ID.unique(),
        fact_id: fact.id,
        text: inputBullet.text,
        target_role: inputBullet.target_role ?? null,
        metric_verified: inputBullet.metric_verified ?? false,
        created_at: timestamp,
        updated_at: timestamp,
      };
      await tables.createRow<FactBulletRow>({
        databaseId: config.databaseId,
        tableId: config.factBulletsTableId,
        rowId: bullet.id,
        data: {
          owner_id: ownerId,
          fact_id: fact.id,
          source_updated_at: timestamp,
          snapshot: JSON.stringify(bullet),
        },
        permissions,
      });
      fact.bullets.push(bullet);
    }
    return fact;
  },

  async archiveFact(factId: string): Promise<void> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const tables = getAppwriteServices().tables;
    const row = await tables.getRow<ProfileFactRow>({
      databaseId: config.databaseId,
      tableId: config.profileFactsTableId,
      rowId: factId,
    });
    const fact = {
      ...parseSnapshot<ProfileFact>(row),
      updated_at: now(),
    };
    await tables.updateRow<ProfileFactRow>({
      databaseId: config.databaseId,
      tableId: config.profileFactsTableId,
      rowId: factId,
      data: {
        archived: true,
        source_updated_at: fact.updated_at,
        snapshot: JSON.stringify(fact),
      },
    });
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

  async extractProfile(file: File): Promise<AgentJob> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const stored = await getAppwriteServices().storage.createFile({
      bucketId: config.resumeFilesBucketId,
      fileId: ID.unique(),
      file,
      permissions: ownerPermissions(getCurrentAppwriteUserId()),
    });
    return createAgentJob("profile_extract", "/profile/extract", {
      file_id: stored.$id,
      filename: file.name,
      replace_existing: false,
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

  // No reviewResume/finalizeResume here on purpose. The agent function renders
  // the PDF before reviewing, and its runtime cannot load WeasyPrint's native
  // libs, so those paths could never succeed. Review and finalize go through the
  // container instead: see api.reviewVersion and api.finalizeVersion, which pair
  // /resumes/render-review with attachReview below.

  tailorResume(
    resumeId: string,
    jobId: string,
    jdParsed: Record<string, unknown>,
    jdClean: string,
  ): Promise<AgentJob> {
    return createAgentJob("resume_tailor", "/resume/tailor", {
      resume_id: resumeId,
      spawned_from_job_id: jobId,
      jd_parsed: jdParsed,
      jd_clean: jdClean,
    });
  },

  // Register a version's stored PDF so `downloadVersionUrl` resolves it right
  // after an agent job returns, without an extra fetch.
  registerVersionFile(version: ResumeVersion): ResumeVersion {
    return rememberVersionFile(version);
  },

  /**
   * Store a review and its rendered PDF against an existing version.
   *
   * The agent function can produce a tailored document but not a PDF, so the
   * browser gets both from the FastAPI container and persists them here. Writes
   * the PDF to the resume bucket, then patches the version snapshot so the
   * editor, Download, and Finalize all see a fully reviewed version.
   */
  async attachReview(
    versionId: string,
    result: {
      review: ResumeReviewResult;
      latex_source: string;
      pdf_base64: string;
    },
  ): Promise<ResumeVersion> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const { tables, storage } = getAppwriteServices();
    const ownerId = getCurrentAppwriteUserId();

    const row = await tables.getRow<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      rowId: versionId,
    });

    const pdfFileId = ID.unique();
    await storage.createFile({
      bucketId: config.resumeFilesBucketId,
      fileId: pdfFileId,
      file: new File([decodeBase64(result.pdf_base64)], `${versionId}.pdf`, {
        type: "application/pdf",
      }),
      permissions: ownerPermissions(ownerId),
    });

    const status = result.review.passed ? "reviewed" : "needs_changes";
    const version: ResumeVersion = {
      ...parseSnapshot<ResumeVersion>(row),
      status,
      review_score: result.review.score,
      review_report: result.review,
      latex_source: result.latex_source,
      updated_at: now(),
    };
    (version as StoredResumeVersion).pdf_file_id = pdfFileId;

    await tables.updateRow<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      rowId: versionId,
      data: {
        // The status column is a strict enum without "needs_changes"; the
        // snapshot above carries the precise status the UI reads.
        status: versionStatusColumn(status),
        source_updated_at: version.updated_at,
        snapshot: JSON.stringify(version),
      },
    });
    return rememberVersionFile(version);
  },

  /** Mark a reviewed version final. Call only after a review has passed. */
  async markFinalized(versionId: string): Promise<ResumeVersion> {
    await ensureAppwriteSession();
    const config = requirePublicAppwriteConfig();
    const tables = getAppwriteServices().tables;
    const row = await tables.getRow<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      rowId: versionId,
    });
    const timestamp = now();
    const version: ResumeVersion = {
      ...parseSnapshot<ResumeVersion>(row),
      status: "final",
      approved_by_user: true,
      finalized_at: timestamp,
      updated_at: timestamp,
    };
    await tables.updateRow<VersionRow>({
      databaseId: config.databaseId,
      tableId: config.resumeVersionsTableId,
      rowId: versionId,
      data: {
        status: versionStatusColumn("final"),
        source_updated_at: timestamp,
        snapshot: JSON.stringify(version),
      },
    });
    return rememberVersionFile(version);
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

  downloadVersionUrl(versionId: string): string {
    const fileId = versionFileIds.get(versionId);
    if (!fileId) return "";
    const config = requirePublicAppwriteConfig();
    return getAppwriteServices().storage.getFileDownload({
      bucketId: config.resumeFilesBucketId,
      fileId,
    });
  },
};
