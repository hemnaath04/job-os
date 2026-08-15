import type { Metadata } from "next";
import { Article } from "../_article";
import { Prose } from "../_prose";

export const metadata: Metadata = {
  title: "Calendar | job.os docs",
  description: "The follow-ups you'd otherwise forget.",
};

export default function CalendarDocsPage() {
  return (
    <Article
      href="/docs/calendar"
      title="Calendar"
      description="The follow-ups you'd otherwise forget."
    >
      <Prose>
        <p>
          Not a full scheduler &mdash; a next-action timeline: Overdue, Today, This week, Later, built
          from the next-action date on each application. Click through to the application to act on it.
        </p>
      </Prose>
    </Article>
  );
}
