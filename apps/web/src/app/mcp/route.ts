import { auth } from "@clerk/nextjs/server";
import { verifyClerkToken } from "@clerk/mcp-tools/next";
import { createMcpHandler, withMcpAuth } from "mcp-handler";
// The MCP SDK's typed tool registration needs Zod v4's Standard Schema shape
// (jsonSchema support). The rest of the app is on Zod v3 for @hookform/resolvers,
// so this is a separately aliased dependency, scoped to this one route.
import { z } from "zod4";
import {
  BackendError,
  callBackend,
  callBackendBinary,
  callBackendMultipart,
  fetchExternalFile,
  toolError,
  toolText,
} from "@/lib/mcp/backend";
import { isAppwritePipelineEnabled, isAppwriteWorkspaceEnabled } from "@/lib/appwrite/config";
import {
  archiveProfileFact,
  archiveResumeCard,
  attachReviewAndMaybeFinalize,
  createApplicationCard,
  createProfileFact,
  downloadResumeVersionFile,
  getResumeTailorJobStatus,
  getResumeVersionSnapshot,
  listProfileFacts,
  listResumeCards,
  mirrorResumeCard,
  mirrorResumeVersionCard,
  patchApplicationCard,
  resolveAppwriteUserId,
  resumeCardExists,
  resumeVersionCardExists,
  resyncResumeCard,
  retargetResumeVersionCard,
  startResumeTailorJob,
} from "@/lib/mcp/appwrite";
import type { Application, Resume, ResumeReviewResult, ResumeVersion } from "@/lib/types";

// Resume tailoring calls Claude and can run long; give tool calls the same
// ceiling the browser's own proxy gets.
export const maxDuration = 300;
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const STATUS = z.enum([
  "wishlist",
  "ready_to_apply",
  "applied",
  "oa_received",
  "interview_scheduled",
  "offer",
  "accepted",
  "rejected",
  "withdrawn",
  "ghosted",
]);

const EMPTY = z.object({});

function token(ctx: { http?: { authInfo?: { token: string } } }): string {
  const t = ctx.http?.authInfo?.token;
  if (!t) throw new BackendError(401, "missing verified access token");
  return t;
}

/**
 * The verified Clerk user id, set by verifyClerkToken alongside the raw
 * bearer token. Needed anywhere this route talks to Appwrite directly
 * (bypassing the FastAPI backend), since Appwrite's own user id is derived
 * from this, not from the bearer token itself.
 */
function clerkUserId(ctx: { http?: { authInfo?: { extra?: { userId?: string } } } }): string {
  const id = ctx.http?.authInfo?.extra?.userId;
  if (!id) throw new BackendError(401, "missing verified user id");
  return id;
}

/**
 * Mirrors a just-written Postgres application into Appwrite, the store the
 * web app's pipeline view actually reads in production
 * (NEXT_PUBLIC_PIPELINE_BACKEND=appwrite). Without this, a job added or
 * moved through this MCP server lands durably in Postgres and never appears
 * on jobs.hemnaath.tech — silently, since the write itself reports success.
 * Best-effort by the same reasoning as the frontend's own dual-write: the
 * Postgres row is the durable one, so a mirror failure here is logged and
 * swallowed rather than failing the tool call.
 */
async function mirrorToAppwrite(op: () => Promise<unknown>, logContext: Record<string, unknown>) {
  if (!isAppwritePipelineEnabled) return;
  try {
    await op();
  } catch (error) {
    console.error("[mcp-appwrite-mirror] failed", { ...logContext, error });
  }
}

/**
 * Same best-effort contract as mirrorToAppwrite, gated on the Resumes/
 * workspace flag instead of the pipeline one — the two go to Appwrite
 * independently (NEXT_PUBLIC_WORKSPACE_BACKEND vs NEXT_PUBLIC_PIPELINE_BACKEND).
 */
async function mirrorToAppwriteWorkspace(
  op: () => Promise<unknown>,
  logContext: Record<string, unknown>,
) {
  if (!isAppwriteWorkspaceEnabled) return;
  try {
    await op();
  } catch (error) {
    console.error("[mcp-appwrite-mirror] failed", { ...logContext, error });
  }
}

/**
 * A resume version's Appwrite mirror points at a resume_id; if the resume
 * container itself was created before this connector started mirroring (or
 * by a tool that still only writes Postgres), that id points at nothing in
 * Appwrite and the version would show up with no resume to belong to. Backed
 * by list_resumes rather than a single-resume GET, since the backend has no
 * such endpoint (only list/patch/delete by id).
 */
async function ensureResumeCardMirrored(jwt: string, appwriteUserId: string, resumeId: string) {
  if (await resumeCardExists(resumeId)) return;
  const resumes = (await callBackend(jwt, "GET", "/resumes")) as Resume[];
  const resume = resumes.find((r) => r.id === resumeId);
  if (resume) await mirrorResumeCard(appwriteUserId, resume);
}

/**
 * Mirrors what the web app's "Add job" dialog does on submit: create the Job,
 * then (only if the caller wants it in the pipeline right away) a second call
 * to create the Application. Shared by add_job_from_url and add_job_from_text
 * so both take the same optional `status` shortcut instead of forcing every
 * caller into a separate create_application round trip.
 */
/**
 * Wraps a just-created resume version with which job/company it actually
 * attached to, in plain words — not just the application_id that was passed
 * in. A wrong or stale ID otherwise attaches silently to someone else's
 * pipeline entry with nothing in the response to make that obvious.
 */
async function withAttachmentEcho(
  jwt: string,
  version: unknown,
  applicationId: string | undefined,
) {
  if (!applicationId) return toolText(version);
  const application = (await callBackend(jwt, "GET", `/applications/${applicationId}`)) as {
    job?: { title?: string; company?: { name?: string } };
    status?: string;
  };
  return toolText({
    version,
    attached_to: {
      application_id: applicationId,
      job_title: application.job?.title,
      company: application.job?.company?.name,
      status: application.status,
    },
  });
}

/**
 * A tailored version's PDF lives in Appwrite Storage under the version id
 * (see attachReviewAndMaybeFinalize) -- fine for the app's own download
 * button, which already knows what it's looking at, but useless handed to a
 * person as a filename. Builds the name someone would actually save it as:
 * candidate, company, role, resolved through whichever of
 * spawned_from_application_id/spawned_from_job_id the version actually has.
 * Falls back to just the candidate's name when neither is set, e.g. a
 * manually finalized master resume with no job attached.
 */
async function resumeVersionFilename(jwt: string, version: ResumeVersion): Promise<string> {
  const candidate = version.json_resume.basics?.name;
  let company: string | undefined;
  let role: string | undefined;
  try {
    if (version.spawned_from_application_id) {
      const application = (await callBackend(
        jwt,
        "GET",
        `/applications/${version.spawned_from_application_id}`,
      )) as { job?: { title?: string; company?: { name?: string } } };
      company = application.job?.company?.name;
      role = application.job?.title;
    } else if (version.spawned_from_job_id) {
      const job = (await callBackend(jwt, "GET", `/jobs/${version.spawned_from_job_id}`)) as {
        title?: string;
        company?: { name?: string };
      };
      company = job.company?.name;
      role = job.title;
    }
  } catch {
    // Best-effort naming only -- an unreachable job/application shouldn't
    // block the download itself.
  }
  const slug = [candidate, company, role]
    .filter((part): part is string => Boolean(part))
    .join(" ")
    .replace(/[\\/:*?"<>|]/g, "")
    .trim()
    .replace(/\s+/g, "_")
    .slice(0, 120);
  return `${slug || "resume"}.pdf`;
}

async function createJobAndMaybeApply(
  jwt: string,
  jobPath: string,
  jobBody: Record<string, unknown>,
  status: string | undefined,
  appwriteUserId: string | undefined,
) {
  const job = (await callBackend(jwt, "POST", jobPath, jobBody)) as { id: string };
  if (!status) return job;
  const application = (await callBackend(jwt, "POST", "/applications", {
    job_id: job.id,
    status,
  })) as Application;
  if (appwriteUserId) {
    await mirrorToAppwrite(() => createApplicationCard(appwriteUserId, application), {
      tool: jobPath,
      application_id: application.id,
    });
  }
  return { job, application };
}

const handler = createMcpHandler(
  (server) => {
    server.registerTool(
      "whoami",
      {
        title: "Who Am I",
        description: "The signed-in job.os user this connector is acting as.",
        inputSchema: EMPTY,
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (_args, ctx) => {
        try {
          return toolText(await callBackend(token(ctx), "GET", "/me"));
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "list_jobs",
      {
        title: "List Jobs",
        description: "List jobs already imported into this user's job.os workspace.",
        inputSchema: EMPTY,
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (_args, ctx) => {
        try {
          return toolText(await callBackend(token(ctx), "GET", "/jobs"));
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "get_job",
      {
        title: "Get Job",
        description: "Get one imported job by id, including its full parsed description.",
        inputSchema: z.object({ job_id: z.string().uuid() }),
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (args, ctx) => {
        try {
          return toolText(await callBackend(token(ctx), "GET", `/jobs/${args.job_id}`));
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "start_resume_tailor",
      {
        title: "Start Resume Tailor",
        description:
          "Kick off the AI tailoring agent for one resume against one job posting (from get_job/search_jobs/add_job_from_url), and return immediately with an agent_job_id rather than waiting for it to finish -- drafting a resume takes real time. Poll get_resume_tailor_status with that id to get the result. Call this as many times as you want for different resumes or jobs; each is its own independent agent job, so several builds genuinely run at once rather than queueing behind each other. The result is a draft resume version, not yet quality-reviewed or finalized.",
        inputSchema: z.object({
          resume_id: z.string().min(1).max(36),
          job_id: z.string().uuid(),
        }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          if (!isAppwriteWorkspaceEnabled) {
            return toolError(new Error("Appwrite workspace is not enabled"));
          }
          const job = (await callBackend(token(ctx), "GET", `/jobs/${args.job_id}`)) as {
            jd_parsed?: Record<string, unknown>;
          };
          const { id } = await startResumeTailorJob(
            resolveAppwriteUserId(clerkUserId(ctx)),
            args.resume_id,
            args.job_id,
            job.jd_parsed ?? {},
            "",
          );
          return toolText({ agent_job_id: id });
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "get_resume_tailor_status",
      {
        title: "Get Resume Tailor Status",
        description:
          "Poll an agent_job_id from start_resume_tailor. status is queued, running, succeeded, or failed; progress (when present) names the current step. On succeeded, output is the new draft resume version (matches the shape list_appwrite_resumes' versions take) -- still a draft, not yet run through the quality review or finalized.",
        inputSchema: z.object({ agent_job_id: z.string().min(1).max(36) }),
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (args, ctx) => {
        try {
          if (!isAppwriteWorkspaceEnabled) {
            return toolError(new Error("Appwrite workspace is not enabled"));
          }
          return toolText(
            await getResumeTailorJobStatus(
              resolveAppwriteUserId(clerkUserId(ctx)),
              args.agent_job_id,
            ),
          );
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "start_resume_finalize",
      {
        title: "Start Resume Finalize",
        description:
          "Run the quality review and produce a final PDF for a drafted resume version (the output of start_resume_tailor), and return immediately with a job_id rather than waiting -- the review takes real time. Poll get_resume_finalize_status with the same version_id and job_id to get the result. Only works on a version job.os drafted itself; a version that was uploaded as a file has no structured resume to review.",
        inputSchema: z.object({
          version_id: z.string().min(1).max(36),
          template_id: z.string().optional(),
        }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          if (!isAppwriteWorkspaceEnabled) {
            return toolError(new Error("Appwrite workspace is not enabled"));
          }
          const appwriteUserId = resolveAppwriteUserId(clerkUserId(ctx));
          const version = await getResumeVersionSnapshot(appwriteUserId, args.version_id);
          if (version.source_filename) {
            return toolError(
              new Error(
                "This version was uploaded as a file, not drafted by job.os, so there is no structured resume to review or finalize.",
              ),
            );
          }
          const facts = await listProfileFacts(appwriteUserId);
          const verifiedFacts = facts
            .filter((fact) => fact.verified)
            .map((fact) => ({
              kind: fact.kind,
              title: fact.title,
              org: fact.org,
              start_date: fact.start_date,
              end_date: fact.end_date,
              location: fact.location,
              source_url: fact.source_url,
              payload: fact.payload,
              bullets: fact.bullets.map((bullet) => ({ text: bullet.text })),
            }));
          const start = (await callBackend(token(ctx), "POST", "/resumes/render-review/start", {
            json_resume: version.json_resume,
            template_key: args.template_id ?? null,
            latex_source: null,
            verified_facts: verifiedFacts,
          })) as { job_id: string };
          return toolText({ job_id: start.job_id, version_id: args.version_id });
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "get_resume_finalize_status",
      {
        title: "Get Resume Finalize Status",
        description:
          "Poll a job_id from start_resume_finalize, with the same version_id. status is running, done, or error. Once done, the result is applied immediately: status finalized means the review passed (or force was set) and the version is now final; status blocked means the review did not pass and the version stays a draft with the review attached so you can see why -- call again with force: true to finalize anyway, matching the web app's 'Finalize anyway' override. Each finished job can only be read once; a repeated call with the same job_id after it already resolved errors with 'not found' rather than reapplying anything.",
        inputSchema: z.object({
          version_id: z.string().min(1).max(36),
          job_id: z.string().min(1),
          force: z.boolean().optional(),
        }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          if (!isAppwriteWorkspaceEnabled) {
            return toolError(new Error("Appwrite workspace is not enabled"));
          }
          const status = (await callBackend(
            token(ctx),
            "GET",
            `/resumes/render-review/status/${args.job_id}`,
          )) as {
            status: "running" | "done" | "error";
            result?: { review: ResumeReviewResult; latex_source: string; pdf_base64: string };
            error?: string;
          };
          if (status.status !== "done" || !status.result) {
            return toolText(status);
          }
          const outcome = await attachReviewAndMaybeFinalize(
            resolveAppwriteUserId(clerkUserId(ctx)),
            args.version_id,
            status.result,
            args.force ?? false,
          );
          return toolText(outcome);
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "add_job_from_url",
      {
        title: "Add Job from URL",
        description:
          "Fetch a job posting URL, parse it, and add it to job.os as a Job. Pass `status` (e.g. \"wishlist\") to also create the pipeline entry in the same call, matching the web app's 'Add to wishlist' button; omit it to get back just the job and call create_application yourself later.",
        inputSchema: z.object({ url: z.string().url(), status: STATUS.optional() }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: true,
        },
      },
      async (args, ctx) => {
        try {
          return toolText(
            await createJobAndMaybeApply(
              token(ctx),
              "/jobs/from-url",
              { url: args.url },
              args.status,
              args.status ? resolveAppwriteUserId(clerkUserId(ctx)) : undefined,
            ),
          );
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "add_job_from_text",
      {
        title: "Add Job from Text",
        description:
          "Add a job posting job.os can't fetch by URL — behind a login, emailed to you, a screenshot you transcribed — by pasting the description text directly. Mirrors the web app's 'Paste the description' tab. Pass `status` (e.g. \"wishlist\") to also create the pipeline entry in the same call; omit it to get back just the job and call create_application yourself later.",
        inputSchema: z.object({
          description: z.string(),
          company: z.string().optional(),
          status: STATUS.optional(),
        }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          return toolText(
            await createJobAndMaybeApply(
              token(ctx),
              "/jobs/from-text",
              { jd_text: args.description, company_hint: args.company },
              args.status,
              args.status ? resolveAppwriteUserId(clerkUserId(ctx)) : undefined,
            ),
          );
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "search_jobs",
      {
        title: "Search Jobs",
        description:
          "Search a pre-built index of postings crawled overnight from Greenhouse, Lever, Ashby and SmartRecruiters (not yet imported) -- fast, no live board fetch. technology_slugs is folded into the free-text query, since the index matches keywords against the full posting body rather than a separate tech-slug filter.",
        inputSchema: z.object({
          title_keywords: z.array(z.string()).optional(),
          technology_slugs: z.array(z.string()).optional(),
          country_codes: z.array(z.string()).optional(),
          max_age_days: z.number().int().min(1).max(180).optional(),
          limit: z.number().int().min(1).max(50).optional(),
        }),
        // POST at the HTTP layer, but it only reads the index and never
        // writes anything to the user's account.
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (args, ctx) => {
        try {
          return toolText(
            await callBackend(token(ctx), "POST", "/index/search", {
              title_keywords: args.title_keywords,
              query: args.technology_slugs?.join(" "),
              country_codes: args.country_codes,
              max_age_days: args.max_age_days,
              limit: args.limit,
            }),
          );
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "list_applications",
      {
        title: "List Applications",
        description: "List this user's pipeline: every job they're tracking, with its status.",
        inputSchema: z.object({ status: STATUS.optional(), archived: z.boolean().optional() }),
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (args, ctx) => {
        try {
          const params = new URLSearchParams();
          if (args.status) params.set("status", args.status);
          if (args.archived !== undefined) params.set("archived", String(args.archived));
          const qs = params.toString();
          return toolText(
            await callBackend(token(ctx), "GET", `/applications${qs ? `?${qs}` : ""}`),
          );
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "get_application",
      {
        title: "Get Application",
        description: "Get one pipeline entry by id.",
        inputSchema: z.object({ application_id: z.string().uuid() }),
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (args, ctx) => {
        try {
          return toolText(
            await callBackend(token(ctx), "GET", `/applications/${args.application_id}`),
          );
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "get_application_timeline",
      {
        title: "Get Application Timeline",
        description: "Get the status-change history for one pipeline entry.",
        inputSchema: z.object({ application_id: z.string().uuid() }),
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (args, ctx) => {
        try {
          return toolText(
            await callBackend(token(ctx), "GET", `/applications/${args.application_id}/timeline`),
          );
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "create_application",
      {
        title: "Create Application",
        description:
          "Add a job (already imported via add_job_from_url or search_jobs + import) to this user's pipeline.",
        inputSchema: z.object({
          job_id: z.string().uuid(),
          status: STATUS.optional(),
          notes: z.string().optional(),
        }),
        // Not idempotent: the backend 409s on a repeat call for the same job_id
        // rather than silently no-op'ing.
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          const application = (await callBackend(
            token(ctx),
            "POST",
            "/applications",
            args,
          )) as Application;
          await mirrorToAppwrite(
            () => createApplicationCard(resolveAppwriteUserId(clerkUserId(ctx)), application),
            { tool: "create_application", application_id: application.id },
          );
          return toolText(application);
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "sync_application_to_appwrite",
      {
        title: "Sync Application to Appwrite",
        description:
          "Repair tool: re-mirrors one pipeline entry into Appwrite from its durable Postgres record. Only needed for applications created before this connector started dual-writing (they exist in job.os's backend but never appeared on jobs.hemnaath.tech). Safe to call again on an already-synced application — it overwrites the mirror with the current Postgres state rather than erroring.",
        inputSchema: z.object({ application_id: z.string().uuid() }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          if (!isAppwritePipelineEnabled) {
            return toolText({ synced: false, reason: "Appwrite pipeline is not enabled" });
          }
          const application = (await callBackend(
            token(ctx),
            "GET",
            `/applications/${args.application_id}`,
          )) as Application;
          const appwriteUserId = resolveAppwriteUserId(clerkUserId(ctx));
          try {
            await createApplicationCard(appwriteUserId, application);
          } catch {
            await patchApplicationCard(appwriteUserId, application.id, {
              status: application.status,
              archived: application.archived,
              notes: application.notes,
            });
          }
          return toolText({ synced: true, application_id: application.id });
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "update_application_status",
      {
        title: "Update Application Status",
        description: "Move a pipeline entry to a new status, e.g. after an interview or offer.",
        inputSchema: z.object({
          application_id: z.string().uuid(),
          status: STATUS,
          notes: z.string().optional(),
        }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          const { application_id, ...patch } = args;
          const result = (await callBackend(
            token(ctx),
            "PATCH",
            `/applications/${application_id}`,
            patch,
          )) as Application;
          await mirrorToAppwrite(async () => {
            const appwriteUserId = resolveAppwriteUserId(clerkUserId(ctx));
            try {
              await patchApplicationCard(appwriteUserId, application_id, patch);
            } catch {
              // No mirror row yet — this application predates dual-writing, or
              // its create mirror failed earlier. Falling back to a full create
              // from the just-patched Postgres state is what makes this
              // self-healing instead of failing the same way every time.
              await createApplicationCard(appwriteUserId, result);
            }
          }, { tool: "update_application_status", application_id });
          return toolText(result);
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "list_resumes",
      {
        title: "List Resumes",
        description:
          "List this user's source resumes (data, not templates) and their tailored versions summary.",
        inputSchema: EMPTY,
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (_args, ctx) => {
        try {
          return toolText(await callBackend(token(ctx), "GET", "/resumes"));
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "create_resume",
      {
        title: "Create Resume",
        description:
          "Create a new, empty resume container to hold versions (e.g. one you'll upload with upload_resume_version). Two kinds: a general-purpose data identity ('SWE resume', 'AI resume' — omit application_id, list_resumes shows what already exists before creating another) or a company-tailored one (pass application_id — name it after the company, base_role after the job title; each company/target job should get its own, never reuse a general resume across companies).",
        inputSchema: z.object({
          name: z.string(),
          base_role: z.string().optional(),
          is_master: z.boolean().optional(),
          application_id: z.string().uuid().optional(),
        }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          const { application_id, ...rest } = args;
          const resume = (await callBackend(token(ctx), "POST", "/resumes", {
            ...rest,
            spawned_from_application_id: application_id,
          })) as Resume;
          await mirrorToAppwriteWorkspace(
            () => mirrorResumeCard(resolveAppwriteUserId(clerkUserId(ctx)), resume),
            { tool: "create_resume", resume_id: resume.id },
          );
          return toolText(resume);
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "upload_resume_version",
      {
        title: "Upload Resume Version",
        description:
          "Push an externally built PDF or DOCX into job.os as a new version under an existing resume (create one first with create_resume if list_resumes is empty). Provide exactly one of content_base64 (inline bytes — fine for small files, but a real PDF can be too large to reliably round-trip through the model's own context) or source_url (an https URL job.os fetches server-side, same pattern as add_job_from_url — use this for anything beyond trivial size). Treated as final immediately — no quality gate to pass, since the caller built and reviewed it themselves. Pass application_id to link it to a specific pipeline entry, retrievable later with get_application_resume_versions.",
        inputSchema: z
          .object({
            resume_id: z.string().uuid(),
            filename: z.string(),
            content_base64: z.string().optional(),
            source_url: z.string().url().optional(),
            note: z.string().optional(),
            application_id: z.string().uuid().optional(),
          })
          .refine((v) => Boolean(v.content_base64) !== Boolean(v.source_url), {
            message: "Provide exactly one of content_base64 or source_url, not both or neither.",
          }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: true,
        },
      },
      async (args, ctx) => {
        try {
          const bytes = args.source_url
            ? (await fetchExternalFile(args.source_url)).bytes
            : Buffer.from(args.content_base64!, "base64");
          const form = new FormData();
          form.set("file", new Blob([new Uint8Array(bytes)]), args.filename);
          if (args.note) form.set("note", args.note);
          if (args.application_id) form.set("application_id", args.application_id);
          const version = (await callBackendMultipart(
            token(ctx),
            `/resumes/${args.resume_id}/versions/upload`,
            form,
          )) as ResumeVersion;
          await mirrorToAppwriteWorkspace(
            async () => {
              const appwriteUserId = resolveAppwriteUserId(clerkUserId(ctx));
              await ensureResumeCardMirrored(token(ctx), appwriteUserId, args.resume_id);
              await mirrorResumeVersionCard(appwriteUserId, version, {
                bytes,
                filename: args.filename,
              });
            },
            { tool: "upload_resume_version", resume_id: args.resume_id, version_id: version.id },
          );
          return withAttachmentEcho(token(ctx), version, args.application_id);
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "create_resume_upload_url",
      {
        title: "Create Resume Upload URL",
        description:
          "Step 1 of pushing a local file into job.os without inlining it or hosting it yourself: returns a short-lived URL to PUT the raw file straight to job.os's storage — e.g. `curl -X PUT --data-binary @/path/to/resume.pdf '<upload_url>'` — plus a `key` to pass to confirm_resume_upload afterward. Prefer this over upload_resume_version's content_base64 for a real file the caller can reach with an outbound request but can't otherwise get to job.os (no public URL, no server of its own).",
        inputSchema: z.object({
          resume_id: z.string().uuid(),
          filename: z.string(),
        }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          return toolText(
            await callBackend(
              token(ctx),
              "POST",
              `/resumes/${args.resume_id}/versions/presign-upload`,
              { filename: args.filename },
            ),
          );
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "confirm_resume_upload",
      {
        title: "Confirm Resume Upload",
        description:
          "Step 2: after PUTting the file to the upload_url from create_resume_upload_url, call this with the same key to check the bytes actually landed and finalize the version — nothing is created until this confirms. Pass application_id to link it to a pipeline entry, same as upload_resume_version.",
        inputSchema: z.object({
          resume_id: z.string().uuid(),
          key: z.string(),
          filename: z.string(),
          note: z.string().optional(),
          application_id: z.string().uuid().optional(),
        }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          const version = (await callBackend(
            token(ctx),
            "POST",
            `/resumes/${args.resume_id}/versions/confirm-upload`,
            {
              key: args.key,
              filename: args.filename,
              note: args.note,
              application_id: args.application_id,
            },
          )) as ResumeVersion;
          await mirrorToAppwriteWorkspace(
            async () => {
              const appwriteUserId = resolveAppwriteUserId(clerkUserId(ctx));
              await ensureResumeCardMirrored(token(ctx), appwriteUserId, args.resume_id);
              const pdf = await callBackendBinary(
                token(ctx),
                `/resumes/${args.resume_id}/versions/${version.id}/download`,
              );
              await mirrorResumeVersionCard(
                appwriteUserId,
                version,
                pdf ? { bytes: pdf.bytes, filename: args.filename } : null,
              );
            },
            { tool: "confirm_resume_upload", resume_id: args.resume_id, version_id: version.id },
          );
          return withAttachmentEcho(token(ctx), version, args.application_id);
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "sync_resume_version_to_appwrite",
      {
        title: "Sync Resume Version to Appwrite",
        description:
          "Repair tool: mirrors one existing resume version (and its parent resume container, if needed) into Appwrite from its durable Postgres record, including copying the PDF into Appwrite Storage. Only needed for versions created before this connector started mirroring resume writes — they exist in job.os's backend but never appeared in Resume Studio. No-ops if the version is already mirrored.",
        inputSchema: z.object({
          resume_id: z.string().uuid(),
          version_id: z.string().uuid(),
        }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          if (!isAppwriteWorkspaceEnabled) {
            return toolText({ synced: false, reason: "Appwrite workspace is not enabled" });
          }
          if (await resumeVersionCardExists(args.version_id)) {
            return toolText({ synced: false, reason: "already synced" });
          }
          const version = (await callBackend(
            token(ctx),
            "GET",
            `/resumes/${args.resume_id}/versions/${args.version_id}`,
          )) as ResumeVersion;
          const appwriteUserId = resolveAppwriteUserId(clerkUserId(ctx));
          await ensureResumeCardMirrored(token(ctx), appwriteUserId, args.resume_id);
          const pdf = await callBackendBinary(
            token(ctx),
            `/resumes/${args.resume_id}/versions/${args.version_id}/download`,
          );
          // Uploaded versions stash the real filename in the json_resume stub
          // (see upload_version in resumes.py), not source_filename.
          const uploadedFilename = (version.json_resume as { filename?: string } | null)
            ?.filename;
          await mirrorResumeVersionCard(
            appwriteUserId,
            version,
            pdf
              ? { bytes: pdf.bytes, filename: version.source_filename ?? uploadedFilename ?? "resume.pdf" }
              : null,
          );
          return toolText({ synced: true, resume_id: args.resume_id, version_id: args.version_id });
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "resync_resume_to_appwrite",
      {
        title: "Resync Resume to Appwrite",
        description:
          "Repair tool: refreshes an already-mirrored resume's Appwrite snapshot from its current Postgres record. Only needed when the resume was corrected directly in Postgres after it was first mirrored (a backfill, a field that didn't exist yet) — the Appwrite copy is frozen at whatever it was when mirrorResumeCard last ran and won't pick up such a change on its own.",
        inputSchema: z.object({ resume_id: z.string().uuid() }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          if (!isAppwriteWorkspaceEnabled) {
            return toolText({ synced: false, reason: "Appwrite workspace is not enabled" });
          }
          const resumes = (await callBackend(token(ctx), "GET", "/resumes")) as Resume[];
          const resume = resumes.find((r) => r.id === args.resume_id);
          if (!resume) throw new BackendError(404, "resume not found");
          await resyncResumeCard(resolveAppwriteUserId(clerkUserId(ctx)), resume);
          return toolText({ synced: true, resume_id: args.resume_id });
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "download_resume_version",
      {
        title: "Download Resume Version",
        description:
          "Downloads a finalized resume version's actual PDF bytes, named for the person, company, and role it was tailored for (e.g. \"Jane_Doe_Acme_Corp_Backend_Engineer.pdf\") instead of the raw version id job.os stores it under internally. Only works once the version has a rendered PDF -- a fresh draft from start_resume_tailor does not; run start_resume_finalize on it first, and check get_resume_finalize_status until it reports final. Returns { status: 'not_ready', reason } instead of the file when there is nothing to download yet.",
        inputSchema: z.object({ version_id: z.string().min(1) }),
        annotations: {
          readOnlyHint: true,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          if (!isAppwriteWorkspaceEnabled) {
            return toolError(new Error("Appwrite resume workspace is not enabled."));
          }
          const appwriteUserId = resolveAppwriteUserId(clerkUserId(ctx));
          const result = await downloadResumeVersionFile(appwriteUserId, args.version_id);
          if (!result) {
            return toolText({
              status: "not_ready",
              reason: "This version has no rendered PDF yet. Run start_resume_finalize on it first.",
            });
          }
          const { version, bytes } = result;
          const filename = await resumeVersionFilename(token(ctx), version);
          return {
            content: [
              {
                type: "text" as const,
                text: JSON.stringify(
                  { filename, version_id: args.version_id, ats_score: version.ats_score },
                  null,
                  2,
                ),
              },
              {
                type: "resource" as const,
                resource: {
                  uri: `file:///${filename}`,
                  mimeType: "application/pdf",
                  blob: bytes.toString("base64"),
                },
              },
            ],
          };
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "list_appwrite_resumes",
      {
        title: "List Appwrite Resumes",
        description:
          "Library-cleanup tool: lists every resume Appwrite actually has for this user, including ones with no Postgres record at all (bulk imports, resumes the browser's own tailoring created directly) — list_resumes only sees Postgres, so it misses these entirely. Use this to find resumes archive_resume can act on.",
        inputSchema: EMPTY,
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (_args, ctx) => {
        try {
          if (!isAppwriteWorkspaceEnabled) {
            return toolText({ enabled: false, resumes: [] });
          }
          const resumes = await listResumeCards(resolveAppwriteUserId(clerkUserId(ctx)));
          return toolText(resumes);
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "archive_resume",
      {
        title: "Archive Resume",
        description:
          "Archives a resume directly in Appwrite (the store the browser reads), including resumes with no Postgres record — list_appwrite_resumes finds the id first. Refuses the master. The resume's versions and files are untouched and stay in storage; this only hides the resume from the active library.",
        // A resume mirrored from Postgres has a UUID id, but one that only ever
        // existed in Appwrite (a bulk import, a browser-created tailor result)
        // has Appwrite's own generated id instead — up to 36 chars, not a UUID.
        inputSchema: z.object({ resume_id: z.string().min(1).max(36) }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: true,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          if (!isAppwriteWorkspaceEnabled) {
            return toolText({ archived: false, reason: "Appwrite workspace is not enabled" });
          }
          await archiveResumeCard(resolveAppwriteUserId(clerkUserId(ctx)), args.resume_id);
          return toolText({ archived: true, resume_id: args.resume_id });
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "move_resume_version",
      {
        title: "Move Resume Version",
        description:
          "Reassign one resume version to a different resume container the user owns — e.g. a company-tailored upload that landed under a generic shared resume instead of getting its own. Create the target first with create_resume if it doesn't exist yet. Does not touch the version's content, status, or attached application, only which resume it belongs to.",
        inputSchema: z.object({
          version_id: z.string().uuid(),
          target_resume_id: z.string().uuid(),
        }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          const version = (await callBackend(
            token(ctx),
            "POST",
            `/resumes/versions/${args.version_id}/move`,
            { target_resume_id: args.target_resume_id },
          )) as ResumeVersion;
          await mirrorToAppwriteWorkspace(
            async () => {
              const appwriteUserId = resolveAppwriteUserId(clerkUserId(ctx));
              await ensureResumeCardMirrored(token(ctx), appwriteUserId, args.target_resume_id);
              await retargetResumeVersionCard(appwriteUserId, args.version_id, args.target_resume_id);
            },
            { tool: "move_resume_version", version_id: args.version_id },
          );
          return toolText(version);
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "get_application_resume_versions",
      {
        title: "Get Application Resume Versions",
        description:
          "List resume versions linked to one pipeline entry (tailor-generated or uploaded via upload_resume_version), newest first.",
        inputSchema: z.object({ application_id: z.string().uuid() }),
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (args, ctx) => {
        try {
          return toolText(
            await callBackend(
              token(ctx),
              "GET",
              `/resumes/versions/by-application/${args.application_id}`,
            ),
          );
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "get_profile_facts",
      {
        title: "Get Profile Facts",
        description:
          "The user's verified career facts (experience, projects, skills, education, certifications). This is the only evidence job.os is allowed to cite when it generates a resume, cover letter, or interview answer.",
        inputSchema: EMPTY,
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (_args, ctx) => {
        try {
          // Production reads and writes facts through Appwrite, not Postgres
          // (NEXT_PUBLIC_WORKSPACE_BACKEND=appwrite) -- the Postgres-backed
          // /profile/facts endpoint is empty for every account whose facts
          // were entered through the web app, same root cause as the resume
          // mirroring elsewhere in this file.
          if (isAppwriteWorkspaceEnabled) {
            return toolText(await listProfileFacts(resolveAppwriteUserId(clerkUserId(ctx))));
          }
          return toolText(await callBackend(token(ctx), "GET", "/profile/facts"));
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "create_profile_fact",
      {
        title: "Create Profile Fact",
        description:
          "Add a career fact (experience, project, skill, education, certification, publication, award, volunteering, or leadership) to the user's profile vault. verified defaults to false. Only get_profile_facts entries with verified: true are ever cited in a tailored resume, so pass verified: true here only when the user has explicitly confirmed the fact themselves in this conversation -- otherwise leave it false and tell them it needs verifying in the web app before job.os will use it. This distinction is the whole point of the vault: it is what keeps a tailored resume from claiming something nobody checked.",
        inputSchema: z.object({
          kind: z.enum([
            "education",
            "experience",
            "project",
            "skill",
            "certification",
            "publication",
            "award",
            "volunteering",
            "leadership",
          ]),
          title: z.string().min(1),
          org: z.string().optional(),
          start_date: z.string().optional(),
          end_date: z.string().optional(),
          location: z.string().optional(),
          payload: z.record(z.string(), z.unknown()).optional(),
          verified: z.boolean().default(false),
          source_url: z.string().optional(),
          bullets: z
            .array(
              z.object({
                text: z.string().min(1),
                target_role: z.string().optional(),
                metric_verified: z.boolean().optional(),
              }),
            )
            .optional(),
        }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: false,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          if (!isAppwriteWorkspaceEnabled) {
            return toolError(new Error("Appwrite workspace is not enabled"));
          }
          const fact = await createProfileFact(resolveAppwriteUserId(clerkUserId(ctx)), args);
          return toolText(fact);
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "archive_profile_fact",
      {
        title: "Archive Profile Fact",
        description:
          "Archive a profile fact. The fact and its bullets are not deleted, only hidden from get_profile_facts and future tailoring.",
        inputSchema: z.object({ fact_id: z.string().min(1).max(36) }),
        annotations: {
          readOnlyHint: false,
          destructiveHint: false,
          idempotentHint: true,
          openWorldHint: false,
        },
      },
      async (args, ctx) => {
        try {
          if (!isAppwriteWorkspaceEnabled) {
            return toolError(new Error("Appwrite workspace is not enabled"));
          }
          await archiveProfileFact(resolveAppwriteUserId(clerkUserId(ctx)), args.fact_id);
          return toolText({ archived: true, fact_id: args.fact_id });
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "list_cover_letters",
      {
        title: "List Cover Letters",
        description: "List cover letters this user has generated, one per job.",
        inputSchema: EMPTY,
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (_args, ctx) => {
        try {
          return toolText(await callBackend(token(ctx), "GET", "/cover-letters"));
        } catch (e) {
          return toolError(e);
        }
      },
    );

    server.registerTool(
      "get_upcoming_calendar",
      {
        title: "Get Upcoming Calendar",
        description:
          "The user's next-action follow-up timeline (overdue, today, this week, later), derived from each application's next-action date.",
        inputSchema: EMPTY,
        annotations: { readOnlyHint: true, openWorldHint: false },
      },
      async (_args, ctx) => {
        try {
          return toolText(await callBackend(token(ctx), "GET", "/calendar/upcoming"));
        } catch (e) {
          return toolError(e);
        }
      },
    );
  },
  { serverInfo: { name: "job.os", version: "1.0.0" } },
);

const authHandler = withMcpAuth(
  handler,
  async (_req, bearerToken) => {
    const clerkAuth = await auth({ acceptsToken: "oauth_token" });
    return verifyClerkToken(clerkAuth, bearerToken);
  },
  { required: true, resourceMetadataPath: "/.well-known/oauth-protected-resource/mcp" },
);

export { authHandler as DELETE, authHandler as GET, authHandler as POST };
