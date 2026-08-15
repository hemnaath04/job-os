import type { Metadata } from "next";
import { Article } from "../_article";
import { Callout, Prose } from "../_prose";

export const metadata: Metadata = {
  title: "Dashboard | job.os docs",
  description: "Where the search stands.",
};

export default function DashboardDocsPage() {
  return (
    <Article href="/docs/dashboard" title="Dashboard" description="Where the search stands.">
      <Prose>
        <p>
          Total applications, response rate, interview conversion, and offers, with a week-over-week
          delta on the number that moves fastest. A daily activity chart and a pipeline-progress gauge
          sit underneath, so a lull is visible before it becomes a problem.
        </p>
        <Callout title="First time here">
          With nothing tracked yet, you get a plain &ldquo;add your first application&rdquo; prompt
          instead of an empty chart. If the data fails to load, the panel says so and gives you a retry,
          rather than pretending the count is zero.
        </Callout>
      </Prose>
    </Article>
  );
}
