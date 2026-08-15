import type { Metadata } from "next";
import { Article } from "../_article";
import { Prose } from "../_prose";

export const metadata: Metadata = {
  title: "Career profile | job.os docs",
  description: "The one vault everything cites.",
};

export default function ProfileDocsPage() {
  return (
    <Article
      href="/docs/profile"
      title="Career profile"
      description="The one vault everything cites."
    >
      <Prose>
        <p>
          Your career facts (roles, projects, education, skills, certifications) each carry bullets, and
          every resume, cover letter, and interview answer job.os generates is only allowed to cite
          what&rsquo;s verified here. Nothing in the vault means nothing gets claimed.
        </p>
        <p>
          Add facts by hand or upload a resume and let the extractor pull them out &mdash; it reports how
          many were new versus already on file, so nothing gets silently duplicated.
        </p>
      </Prose>
    </Article>
  );
}
