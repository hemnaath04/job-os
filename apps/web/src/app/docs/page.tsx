import type { Metadata } from "next";
import Link from "next/link";
import { Article } from "./_article";
import { H2, Prose } from "./_prose";

export const metadata: Metadata = {
  title: "Introduction | job.os docs",
  description: "What job.os does, and how the pieces fit together.",
};

export default function DocsIndexPage() {
  return (
    <Article
      href="/docs"
      title="Introduction"
      description="What job.os does, and how the pieces fit together."
      toc={[
        { id: "why-one-vault", label: "Why one vault" },
        { id: "whats-in-here", label: "What's in here" },
      ]}
    >
      <Prose>
        <p>
          job.os is one workspace for the co-op and new-grad grind: a pipeline board, an AI resume
          tailor, and a discovery feed, all reading from a single vault of career facts you&rsquo;ve
          verified yourself.
        </p>

        <H2 id="why-one-vault">Why one vault</H2>
        <p>
          The resume tailor, the cover-letter writer, and interview prep all draw from the exact same
          verified <Link href="/docs/profile">profile</Link>. None of them can invent a skill or a number
          that isn&rsquo;t backed by a bullet you added yourself. If the evidence isn&rsquo;t there, the UI
          raises a gap question instead of quietly filling it in. The resume you send is one you can
          defend in the interview.
        </p>

        <H2 id="whats-in-here">What&rsquo;s in here</H2>
        <ul>
          <li>
            <Link href="/docs/quickstart">Quick start</Link> &mdash; verify your facts, find or add a
            job, tailor a resume, track it.
          </li>
          <li>
            <Link href="/docs/dashboard">Dashboard</Link>, <Link href="/docs/applications">Applications</Link>,{" "}
            <Link href="/docs/calendar">Calendar</Link> &mdash; where the search stands and the pipeline
            itself.
          </li>
          <li>
            <Link href="/docs/jobs">Job Finder</Link>, <Link href="/docs/tailor">AI Resume Tailor</Link>,{" "}
            <Link href="/docs/interview">Interview Prep</Link> &mdash; the pipeline tools.
          </li>
          <li>
            <Link href="/docs/resumes">Resumes</Link>, <Link href="/docs/cover-letters">Cover Letters</Link>,{" "}
            <Link href="/docs/profile">Profile</Link> &mdash; the documents and the vault behind them.
          </li>
          <li>
            <Link href="/docs/settings">Settings</Link> &mdash; defaults that seed discovery and
            tailoring.
          </li>
          <li>
            <Link href="/docs/mcp">MCP connector</Link> &mdash; connect Claude Code or any MCP client to
            your own job.os data.
          </li>
        </ul>
      </Prose>
    </Article>
  );
}
