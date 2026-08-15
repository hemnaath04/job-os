import type { Metadata } from "next";
import { Article } from "../_article";
import { Callout, Prose } from "../_prose";

export const metadata: Metadata = {
  title: "AI Resume Tailor | job.os docs",
  description: "Tuned to the posting, not invented for it.",
};

export default function TailorDocsPage() {
  return (
    <Article
      href="/docs/tailor"
      title="AI Resume Tailor"
      description="Tuned to the posting, not invented for it."
    >
      <Prose>
        <p>
          Point it at a job and it iterates: draft, score against the posting, revise, until it clears
          the match target or runs out of requirements your verified profile can actually back. Every
          version keeps its ATS score and QA review score, so you can see the trail that got you there.
        </p>
        <Callout title="Why a run takes a little while">
          The same call fetches the posting and asks the model to write. The progress bar tracks real
          stage updates when they arrive, and falls back to a typical-run-timing estimate (labeled as
          such) when they don&rsquo;t, so it never reads as frozen.
        </Callout>
      </Prose>
    </Article>
  );
}
