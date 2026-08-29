import type { Metadata } from "next";
import Link from "next/link";
import { Article } from "../_article";
import { Prose } from "../_prose";

export const metadata: Metadata = {
  title: "Settings | job.os docs",
  description: "Set your defaults once.",
};

export default function SettingsDocsPage() {
  return (
    <Article href="/docs/settings" title="Settings" description="Set your defaults once.">
      <Prose>
        <p>
          Target roles and seniority range, work authorization, default discovery filters, salary
          floor, target and excluded companies, and your timezone. Everything here seeds{" "}
          <Link href="/docs/jobs">Job Finder</Link> and the <Link href="/docs/tailor">tailor</Link>,
          so you&rsquo;re not re-entering it per search.
        </p>
        <p>
          Work authorization is the one worth a minute. Answered, it is compared against what a
          posting actually requires, and a role that could not hire you is stopped before a resume
          is written for it. Left blank, nothing is ever stopped: no answer is assumed on your
          behalf, because a job wrongly skipped is one you never hear about.
        </p>
      </Prose>
    </Article>
  );
}
