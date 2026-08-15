import type { Metadata } from "next";
import Link from "next/link";
import { Article } from "../_article";
import { Prose } from "../_prose";

export const metadata: Metadata = {
  title: "Cover Letters | job.os docs",
  description: "Every sentence cites a bullet.",
};

export default function CoverLettersDocsPage() {
  return (
    <Article
      href="/docs/cover-letters"
      title="Cover Letters"
      description="Every sentence cites a bullet."
    >
      <Prose>
        <p>
          Pick a job and a tone (plain, warm, or direct, never &ldquo;enthusiastic&rdquo;), and it writes
          two passes &mdash; the second only runs if the first left something worth fixing. Every claim
          shows the bullet it came from inline.
        </p>
        <p>
          What the <Link href="/docs/profile">profile</Link> can&rsquo;t back becomes an open question,
          shown next to the letter, not a sentence it quietly made up. Anything it drafted and then cut
          for lack of evidence is listed too, struck through, with the reason.
        </p>
      </Prose>
    </Article>
  );
}
