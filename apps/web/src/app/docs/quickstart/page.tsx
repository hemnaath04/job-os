import type { Metadata } from "next";
import Link from "next/link";
import { Article } from "../_article";
import { Step, Steps } from "../_prose";

export const metadata: Metadata = {
  title: "Quick start | job.os docs",
  description: "Verify your facts, find or add a job, tailor a resume, track it.",
};

export default function QuickstartPage() {
  return (
    <Article
      href="/docs/quickstart"
      title="Quick start"
      description="Verify your facts, find or add a job, tailor a resume, track it."
    >
      <Steps>
        <Step n={1} title="Verify your facts">
          Upload a resume on the <Link href="/docs/profile">Profile</Link> page, or add facts by hand.
          This is the only source every generated document is allowed to cite.
        </Step>
        <Step n={2} title="Find or add a job">
          Search on <Link href="/docs/jobs">Job Finder</Link>, or paste a URL or description straight
          into <Link href="/docs/applications">Applications</Link>.
        </Step>
        <Step n={3} title="Tailor a resume for it">
          One click from either page opens the <Link href="/docs/tailor">Resume Tailor</Link>, which
          iterates until it hits the match target or runs out of ground your profile covers.
        </Step>
        <Step n={4} title="Track it, then prep">
          Move the card as things progress in <Link href="/docs/applications">Applications</Link>, and
          generate an <Link href="/docs/interview">Interview Prep</Link> pack once you have a screen on
          the <Link href="/docs/calendar">Calendar</Link>.
        </Step>
      </Steps>
    </Article>
  );
}
