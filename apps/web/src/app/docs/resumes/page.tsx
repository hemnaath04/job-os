import type { Metadata } from "next";
import Link from "next/link";
import { Article } from "../_article";
import { Prose } from "../_prose";

export const metadata: Metadata = {
  title: "Resume studio | job.os docs",
  description: "One master, many tailored versions.",
};

export default function ResumesDocsPage() {
  return (
    <Article
      href="/docs/resumes"
      title="Resume studio"
      description="One master, many tailored versions."
    >
      <Prose>
        <p>
          Your source resumes hold the data (experience, skills); templates hold the look (LaTeX only,
          never your data). <Link href="/docs/tailor">Tailoring</Link> combines the two and saves the
          result as a new version under the source resume &mdash; the template itself never changes.
        </p>
        <p>
          Upload a <code>.tex</code> file to keep a design exactly, or a PDF to have it reverse-engineered
          into one (described honestly in the UI as &ldquo;comes close,&rdquo; not a promise of a pixel
          match).
        </p>
      </Prose>
    </Article>
  );
}
