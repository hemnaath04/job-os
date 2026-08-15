import type { Metadata } from "next";
import Link from "next/link";
import { Article } from "../_article";
import { Prose } from "../_prose";

export const metadata: Metadata = {
  title: "Job Finder | job.os docs",
  description: "Search, don't just browse.",
};

export default function JobsDocsPage() {
  return (
    <Article href="/docs/jobs" title="Job Finder" description="Search, don't just browse.">
      <Prose>
        <p>
          Type a plain-English query like &ldquo;fullstack intern in Boston, last two weeks&rdquo; and an
          agent turns it into structured filters, or set the filters yourself and pick which sources to
          search &mdash; the free boards run by default, add a key for wider coverage.
        </p>
        <p>
          Every result carries a fit score computed against your verified{" "}
          <Link href="/docs/profile">profile</Link>, plus any eligibility flags the posting text implies.
          One click either imports a job to your Wishlist, or imports it and sends you straight into{" "}
          <Link href="/docs/tailor">tailoring</Link> for it.
        </p>
      </Prose>
    </Article>
  );
}
