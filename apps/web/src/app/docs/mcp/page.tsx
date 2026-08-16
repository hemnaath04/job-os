import type { Metadata } from "next";
import { Article } from "../_article";
import { H2, Prose } from "../_prose";

export const metadata: Metadata = {
  title: "MCP connector | job.os docs",
  description: "Connect Claude Code or any MCP client to your own job.os data.",
};

function Code({ children }: { children: string }) {
  return (
    <pre className="not-prose overflow-x-auto rounded-[var(--radius-card)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-4 text-[13px] leading-relaxed text-[color:var(--color-text)]">
      <code>{children}</code>
    </pre>
  );
}

const TOOL_GROUPS = [
  {
    title: "Jobs",
    tools: "list_jobs, get_job, add_job_from_url, add_job_from_text, search_jobs",
  },
  {
    title: "Applications",
    tools:
      "list_applications, get_application, get_application_timeline, create_application, update_application_status",
  },
  {
    title: "Documents",
    tools:
      "list_resumes, create_resume, upload_resume_version, get_application_resume_versions, get_profile_facts, list_cover_letters",
  },
  { title: "Other", tools: "whoami, get_upcoming_calendar" },
];

export default function McpDocsPage() {
  return (
    <Article
      href="/docs/mcp"
      title="MCP connector"
      description="Connect Claude Code or any MCP client to your own job.os data."
      toc={[
        { id: "connect", label: "Connect from Claude Code" },
        { id: "tools", label: "What's exposed" },
        { id: "for-your-users", label: "Add it for your own users" },
      ]}
    >
      <Prose>
        <p>
          job.os exposes an MCP server at <code>https://jobs.hemnaath.tech/mcp</code>, gated by OAuth the
          same way Atlassian&rsquo;s or Figma&rsquo;s connectors are &mdash; you connect once, sign in,
          approve a consent screen, and the client acts as you from then on. Nothing is shared across
          users: every tool call is scoped to whichever job.os account you signed in with.
        </p>

        <H2 id="connect">Connect from Claude Code</H2>
        <Code>{"claude mcp add --transport http job-os https://jobs.hemnaath.tech/mcp"}</Code>
        <p>Then, in a real terminal (not a non-interactive shell), run:</p>
        <Code>{"claude mcp login job-os"}</Code>
        <p>
          That opens your browser to sign in and approve the consent screen. Once it confirms, run{" "}
          <code>/mcp</code> inside any Claude Code session to see <code>job-os</code> connected.
        </p>

        <H2 id="tools">What&rsquo;s exposed</H2>
        <p>
          Eighteen tools, covering jobs, applications, resumes, profile facts, cover letters, and
          calendar. Most are pure reads; the writes (<code>add_job_from_url</code>,{" "}
          <code>add_job_from_text</code>, <code>create_application</code>,{" "}
          <code>update_application_status</code>, <code>create_resume</code>,{" "}
          <code>upload_resume_version</code>) are additive or reversible &mdash; nothing destructive.
          Both <code>add_job_from_url</code> and <code>add_job_from_text</code> take an optional{" "}
          <code>status</code> to create the pipeline entry in the same call, matching the web
          app&rsquo;s &ldquo;Add to wishlist&rdquo; button.
        </p>
        <p>
          <code>upload_resume_version</code> takes either <code>content_base64</code> (inline
          bytes) or <code>source_url</code> (an https URL job.os fetches itself, same pattern as{" "}
          <code>add_job_from_url</code>) &mdash; use <code>source_url</code> for anything beyond a
          trivially small file, since inlining a real PDF as base64 can be too large to reliably
          round-trip through a model&rsquo;s own context.
        </p>
        <div className="not-prose grid gap-3 sm:grid-cols-2">
          {TOOL_GROUPS.map((g) => (
            <div
              key={g.title}
              className="rounded-[var(--radius-card)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] p-4"
            >
              <p className="text-sm font-medium text-[color:var(--color-text)]">{g.title}</p>
              <p className="mt-1 font-mono text-xs leading-relaxed text-[color:var(--color-text-muted)]">
                {g.tools}
              </p>
            </div>
          ))}
        </div>

        <H2 id="for-your-users">Add it for your own users</H2>
        <p>Anyone can add job.os as a plugin marketplace, no client credentials needed:</p>
        <Code>{"/plugin marketplace add hemnaath04/job-os\n/plugin install job-os@job-os"}</Code>
      </Prose>
    </Article>
  );
}
