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
  callBackendMultipart,
  fetchExternalFile,
  toolError,
  toolText,
} from "@/lib/mcp/backend";

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
 * Mirrors what the web app's "Add job" dialog does on submit: create the Job,
 * then (only if the caller wants it in the pipeline right away) a second call
 * to create the Application. Shared by add_job_from_url and add_job_from_text
 * so both take the same optional `status` shortcut instead of forcing every
 * caller into a separate create_application round trip.
 */
async function createJobAndMaybeApply(
  jwt: string,
  jobPath: string,
  jobBody: Record<string, unknown>,
  status: string | undefined,
) {
  const job = (await callBackend(jwt, "POST", jobPath, jobBody)) as { id: string };
  if (!status) return job;
  const application = await callBackend(jwt, "POST", "/applications", {
    job_id: job.id,
    status,
  });
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
          "Search job boards for new postings (not yet imported). Merges results across sources and sorts by recency.",
        inputSchema: z.object({
          title_keywords: z.array(z.string()).optional(),
          technology_slugs: z.array(z.string()).optional(),
          country_codes: z.array(z.string()).optional(),
          max_age_days: z.number().int().min(1).max(180).optional(),
          limit: z.number().int().min(1).max(50).optional(),
        }),
        // POST at the HTTP layer, but it only queries job boards and never
        // writes anything to the user's account.
        annotations: { readOnlyHint: true, openWorldHint: true },
      },
      async (args, ctx) => {
        try {
          return toolText(await callBackend(token(ctx), "POST", "/discovery/search", args));
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
          return toolText(await callBackend(token(ctx), "POST", "/applications", args));
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
          return toolText(
            await callBackend(token(ctx), "PATCH", `/applications/${application_id}`, patch),
          );
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
          "Create a new, empty resume container to hold versions (e.g. one you'll upload with upload_resume_version). This is the data identity — 'SWE resume', 'Research resume' — not a file; list_resumes shows what already exists before creating another.",
        inputSchema: z.object({
          name: z.string(),
          base_role: z.string().optional(),
          is_master: z.boolean().optional(),
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
          return toolText(await callBackend(token(ctx), "POST", "/resumes", args));
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
          const version = await callBackendMultipart(
            token(ctx),
            `/resumes/${args.resume_id}/versions/upload`,
            form,
          );

          if (!args.application_id) return toolText(version);

          // Echo back which job/company this actually landed on, in plain
          // words, not just the application_id that was passed in — a wrong
          // or stale ID silently attaches to someone else's pipeline entry
          // otherwise, with nothing in the response to make that obvious.
          const application = (await callBackend(
            token(ctx),
            "GET",
            `/applications/${args.application_id}`,
          )) as { job?: { title?: string; company?: { name?: string } }; status?: string };
          return toolText({
            version,
            attached_to: {
              application_id: args.application_id,
              job_title: application.job?.title,
              company: application.job?.company?.name,
              status: application.status,
            },
          });
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
          return toolText(await callBackend(token(ctx), "GET", "/profile/facts"));
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
