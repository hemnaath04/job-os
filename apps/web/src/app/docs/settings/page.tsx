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
          Target roles and seniority range, work authorization (so ineligible roles get filtered out
          rather than wasting your time), default discovery filters, salary floor, target and excluded
          companies, and your timezone. Everything here seeds <Link href="/docs/jobs">Job Finder</Link>{" "}
          and the <Link href="/docs/tailor">tailor</Link>, so you&rsquo;re not re-entering it per search.
        </p>
      </Prose>
    </Article>
  );
}
