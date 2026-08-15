import { auth } from "@clerk/nextjs/server";
import { verifyClerkToken } from "@clerk/mcp-tools/next";
import { createMcpHandler, withMcpAuth } from "mcp-handler";
// The MCP SDK's typed tool registration needs Zod v4's Standard Schema shape
// (jsonSchema support). The rest of the app is on Zod v3 for @hookform/resolvers,
// so this is a separately aliased dependency, scoped to this one route.
import { z } from "zod4";
import { BackendError, callBackend, toolError, toolText } from "@/lib/mcp/backend";

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
          "Fetch a job posting URL, parse it, and add it to job.os as a Job (not yet in the pipeline). Returns the created job; pass its id to create_application to add it to the Wishlist.",
        inputSchema: z.object({ url: z.string().url() }),
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
            await callBackend(token(ctx), "POST", "/jobs/from-url", { url: args.url }),
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
