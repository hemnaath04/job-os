import type { Metadata } from "next";
import { Article } from "../_article";
import { H2, Prose } from "../_prose";

export const metadata: Metadata = {
  title: "MCP connector | job.os docs",
  description: "Connect Claude Code, OpenClaw, or any MCP client to your own job.os data.",
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
      "list_applications, get_application, get_application_timeline, create_application, update_application_status, sync_application_to_appwrite",
  },
  {
    title: "Resume tailoring",
    tools:
      "start_resume_tailor, get_resume_tailor_status, start_resume_finalize, get_resume_finalize_status, download_resume_version",
  },
  {
    title: "Resume library",
    tools:
      "list_resumes, list_appwrite_resumes, create_resume, archive_resume, move_resume_version, get_application_resume_versions",
  },
  {
    title: "Resume files",
    tools:
      "upload_resume_version, create_resume_upload_url, confirm_resume_upload, sync_resume_version_to_appwrite, resync_resume_to_appwrite",
  },
  {
    title: "Other",
    tools:
      "whoami, get_profile_facts, create_profile_fact, update_profile_fact, patch_bullet, archive_profile_fact, list_cover_letters, get_upcoming_calendar",
  },
];

const AGENT_PROMPT = `You have access to job.os, a personal job-search platform, through MCP. Use it to search for roles, track applications, and draft tailored resumes for the person who authorized this connection -- every tool call is scoped to their account alone.

Typical workflow:
1. Find postings with search_jobs (a pre-built index crawled overnight, not a live board fetch, so it answers fast), or add a specific posting the person gives you with add_job_from_url / add_job_from_text. Pass status to also create the pipeline entry in the same call.
2. Check list_resumes for their existing resumes (there is usually a master and a handful of company-specific ones). Use get_profile_facts to see what is actually verified about them -- never claim experience, a metric, or a technology that is not backed by a verified fact. If a job wants something the facts do not support, say so instead of inventing it, or capture it with create_profile_fact first (see the rule on verified below).
3. Call start_resume_tailor(resume_id, job_id) to draft a resume tailored to one job. It returns immediately with an agent_job_id; it does not wait for the draft to finish.
4. Poll get_resume_tailor_status(agent_job_id) every few seconds until status is "succeeded" or "failed". You can start several tailor runs back to back, for different jobs or different resumes, and poll each independently -- they run as genuinely concurrent agent jobs, not one after another.
5. The result of a succeeded job is a draft resume version, an id you get from that response. To make it final: call start_resume_finalize(version_id) for a job_id, then poll get_resume_finalize_status(version_id, job_id) the same way. Once done, the version is either finalized (the review passed) or blocked (it did not; the review is attached so you can see why, and you can call the status tool again with force: true to finalize anyway).
6. Once a version is finalized, download_resume_version(version_id) returns the actual PDF, named after the candidate, company, and role it was tailored for rather than the raw version id. It answers { status: "not_ready" } instead of a file if the version has no rendered PDF yet.
7. Use list_applications / get_application_timeline to check pipeline status, and update_application_status to move something forward once the person confirms.

Rules:
- Nothing here is destructive. archive_resume, move_resume_version, and archive_profile_fact never delete data, only hide or relocate it.
- update_profile_fact corrects a fact's fields, most usefully payload.keywords, which is what decides how a project ranks against a posting: a project with no technologies listed cannot match a job description written in technology nouns, however relevant it is. It cannot set verified.
- patch_bullet changes one bullet's wording, for tightening a bullet past 30 words or varying a repeated opening verb. It refuses an edit that introduces a number the fact does not already state, or that grows the bullet past its current length or the cap. Those are the rules the tailor is held to, and they bind harder here: a tailored resume is one document, an edit to the vault is inherited by every resume after it. If a bullet needs a metric it does not have, ask for it.
- create_profile_fact defaults verified to false. Only pass verified: true when the person has explicitly confirmed the fact themselves in this conversation: an unverified fact is never cited in a tailored resume, and a wrongly-verified one corrupts every resume tailored after it.
- If a tool call fails with "not found" for an id you were given, do not retry with a guessed id -- ask instead.
- Report what you actually did (which tools, which ids) rather than a generic "I tailored your resume," so the person can verify it in the web app.`;

export default function McpDocsPage() {
  return (
    <Article
      href="/docs/mcp"
      title="MCP connector"
      description="Connect Claude Code, OpenClaw, or any MCP client to your own job.os data."
      toc={[
        { id: "connect-claude", label: "Connect from Claude Code" },
        { id: "connect-any", label: "Connect from any MCP client" },
        { id: "how-auth-works", label: "How the OAuth flow works" },
        { id: "troubleshooting", label: "When the connection fails" },
        { id: "agent-prompt", label: "Agent instructions" },
        { id: "tools", label: "What's exposed" },
        { id: "for-your-users", label: "Add it for your own users" },
      ]}
    >
      <Prose>
        <p>
          job.os exposes an MCP server at <code>https://jobs.hemnaath.tech/mcp</code>, gated by
          standard MCP OAuth: the same discovery-and-consent flow Atlassian&rsquo;s or
          Figma&rsquo;s connectors use, and the same one the{" "}
          <a
            href="https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization"
            target="_blank"
            rel="noreferrer noopener"
          >
            MCP Authorization spec
          </a>{" "}
          defines. Nothing here is Claude-specific under the hood: any client that speaks that
          spec can discover the server at <code>/.well-known/oauth-authorization-server</code>{" "}
          and <code>/.well-known/oauth-protected-resource/mcp</code>, run the OAuth flow, and call
          the same tools. You connect once, sign in, approve a consent screen, and the client acts
          as you from then on, scoped to your account only.
        </p>

        <H2 id="connect-claude">Connect from Claude Code</H2>
        <Code>{"claude mcp add --transport http job-os https://jobs.hemnaath.tech/mcp"}</Code>
        <p>Then, in a real terminal (not a non-interactive shell), run:</p>
        <Code>{"claude mcp login job-os"}</Code>
        <p>
          That opens your browser to sign in and approve the consent screen. Once it confirms, run{" "}
          <code>/mcp</code> inside any Claude Code session to see <code>job-os</code> connected.
        </p>

        <H2 id="connect-any">Connect from any MCP client</H2>
        <p>
          Point any MCP-compliant client at the streamable-HTTP endpoint below and let it drive
          OAuth discovery itself:
        </p>
        <Code>{"https://jobs.hemnaath.tech/mcp"}</Code>
        <p>
          As one concrete example,{" "}
          <a href="https://docs.openclaw.ai/cli/mcp" target="_blank" rel="noreferrer noopener">
            OpenClaw
          </a>{" "}
          registers an outbound MCP server and its OAuth credentials with:
        </p>
        <Code>
          {"openclaw mcp add job-os \\\n  --url https://jobs.hemnaath.tech/mcp \\\n  --transport streamable-http \\\n  --auth oauth\n\nopenclaw mcp login job-os"}
        </Code>
        <p>
          A model on its own, such as one of the Hermes releases, does not connect to an MCP
          server directly: the agent harness running that model is the actual MCP client, and
          whichever harness you use, the same URL and the same OAuth flow apply. If your
          client&rsquo;s connect flow needs a name for it, <code>job-os</code> is as good as any;
          nothing about the server cares what the client calls it.
        </p>

        <H2 id="how-auth-works">How the OAuth flow works</H2>
        <p>
          You do not need any of this to connect a compliant client, which handles all of it.
          It matters if you are writing your own client, or working out why one is failing.
        </p>
        <ol>
          <li>
            Call <code>POST /mcp</code> with no token. It answers <code>401</code> with a{" "}
            <code>WWW-Authenticate</code> header naming the metadata document.
          </li>
          <li>
            Fetch <code>/.well-known/oauth-protected-resource/mcp</code>. It names the
            authorization server, <code>clerk.jobs.hemnaath.tech</code>, and the scopes to ask
            for.
          </li>
          <li>
            <code>POST</code> to that server&rsquo;s <code>/oauth/register</code>. Dynamic client
            registration is open, so this needs no credentials and returns a{" "}
            <code>client_id</code>.
          </li>
          <li>
            Send the person to <code>/oauth/authorize</code> with PKCE (<code>S256</code>). They
            sign in and approve a consent screen.
          </li>
          <li>
            Exchange the code at <code>/oauth/token</code>. Public clients may exchange without a{" "}
            <code>client_secret</code>, so a CLI or desktop client needs no secret of its own.
          </li>
          <li>
            Send the token as <code>Authorization: Bearer</code> on every call.
          </li>
        </ol>
        <p>
          The scopes requested are <code>profile</code>, <code>email</code> and{" "}
          <code>offline_access</code>, and no more. The first two say who you are; every tool
          scopes its reads and writes to that person and nothing else. The third is what keeps
          the connection alive: an access token expires after a day and a refresh token never
          does, so without it an unattended agent would stop working every 24 hours and need
          someone back at a browser. <code>public_metadata</code> and{" "}
          <code>private_metadata</code> are deliberately not requested, because no tool here
          reads them.
        </p>
        <p>
          <strong>A human has to sign in once, per client.</strong> There is no
          machine-to-machine path: only the authorization-code and refresh-token grants exist,
          and the server accepts nothing but an OAuth access token, so there are no API keys to
          issue. That is the point rather than a limitation. The token is issued to you, and an
          agent holding it can reach your data and no one else&rsquo;s.
        </p>

        <H2 id="troubleshooting">When the connection fails</H2>
        <p>
          Almost every failure lands your browser on a <code>/callback</code> URL carrying an{" "}
          <code>error</code> and an <code>error_description</code>. Read that description: it is
          far more specific than whatever the client prints.
        </p>
        <p>
          <code>invalid_scope</code> means the client asked for something it is not allowed. The
          usual cause is a client that requests <code>openid</code> out of habit, since most
          OpenID Connect clients always do. This server does not advertise it, because the
          authorization server grants no client that scope, and asking for it fails only{" "}
          <em>after</em> you have signed in. If your client insists on sending it, the fix is on
          the Clerk side: assign <code>openid</code> to the instance&rsquo;s default scopes, then
          re-register the client, since an existing registration keeps the scopes it was issued
          with.
        </p>
        <p>
          Worth knowing if you are debugging your own client: the authorization server&rsquo;s{" "}
          <code>scopes_supported</code> lists what the instance supports, which is wider than
          what any client may actually request. The <code>scope</code> returned by{" "}
          <code>/oauth/register</code> is the real list.
        </p>
        <p>
          A failed attempt leaves a registered client cached, so clear it before retrying, or the
          client will repeat the request that failed. In Claude Code that is{" "}
          <code>claude mcp logout job-os</code>; elsewhere, remove and re-add the connection.
        </p>

        <H2 id="agent-prompt">Agent instructions</H2>
        <p>
          Once connected, an agent still has to know <em>how</em> to use the tools well. Drop
          this into its system prompt, its <code>AGENTS.md</code>, or wherever it reads standing
          instructions from:
        </p>
        <Code>{AGENT_PROMPT}</Code>

        <H2 id="tools">What&rsquo;s exposed</H2>
        <p>
          Thirty-one tools, covering jobs, applications, resume tailoring, the resume library,
          resume files, profile facts, cover letters, and calendar. Most are pure reads; the
          writes are additive or reversible, nothing destructive, and nothing here can touch
          another user&rsquo;s data. <code>start_resume_tailor</code>/<code>get_resume_tailor_status</code>{" "}
          and <code>start_resume_finalize</code>/<code>get_resume_finalize_status</code> are both
          start/poll pairs: the first call returns a job id immediately instead of blocking on
          work that takes real time, and each job runs independently, so several run
          concurrently rather than queueing behind each other.
        </p>
        <p>
          Both <code>add_job_from_url</code> and <code>add_job_from_text</code> take an optional{" "}
          <code>status</code> to create the pipeline entry in the same call, matching the web
          app&rsquo;s &ldquo;Add to wishlist&rdquo; button.{" "}
          <code>upload_resume_version</code> takes either <code>content_base64</code> (inline
          bytes) or <code>source_url</code> (an https URL job.os fetches itself, same pattern as{" "}
          <code>add_job_from_url</code>): use <code>source_url</code> for anything beyond a
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
