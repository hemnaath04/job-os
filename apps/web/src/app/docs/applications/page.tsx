import type { Metadata } from "next";
import Link from "next/link";
import { Article } from "../_article";
import { Callout, Prose } from "../_prose";

export const metadata: Metadata = {
  title: "Applications | job.os docs",
  description: "The pipeline, as a board or a table.",
};

export default function ApplicationsDocsPage() {
  return (
    <Article
      href="/docs/applications"
      title="Applications"
      description="The pipeline, as a board or a table."
    >
      <Prose>
        <p>
          Kanban by default: Wishlist, Applied, Interview, Rejected, Offer. Drag a card to change its
          status, or work the same data as a sortable table when you want the exact status (Applied vs.
          OA received vs. Rejected vs. Withdrawn) instead of the merged column view.
        </p>
        <p>
          Double-click a card to open the original posting. &ldquo;Tailor&rdquo; jumps straight into the{" "}
          <Link href="/docs/tailor">resume tailor</Link> for that job. Archiving is undo-able from the
          confirmation toast &mdash; nothing is deleted outright.
        </p>
        <Callout title="Adding a job manually">
          &ldquo;Add job&rdquo; opens a dialog to add a role either by pasting a URL (the server fetches
          and parses it) or pasting the raw job description text, for postings behind a login.
        </Callout>
      </Prose>
    </Article>
  );
}
