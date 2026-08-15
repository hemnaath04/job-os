import type { Metadata } from "next";
import { Article } from "../_article";
import { Prose } from "../_prose";

export const metadata: Metadata = {
  title: "Interview Prep | job.os docs",
  description: "Rehearse your own resume, not just the role.",
};

export default function InterviewDocsPage() {
  return (
    <Article
      href="/docs/interview"
      title="Interview Prep"
      description="Rehearse your own resume, not just the role."
    >
      <Prose>
        <p>
          Generates a prep pack from the job, your tailored resume, and your verified profile: questions
          about your own bullets (the category almost nobody rehearses), technical, behavioural, and
          questions to ask them. Each answer is a STAR scaffold built only from evidence you&rsquo;ve
          verified.
        </p>
        <p>
          A Readiness score (0-100) is computed from must-have topic coverage, kept separate from the
          model&rsquo;s own self-estimate so the two are never confused for one grade.
        </p>
      </Prose>
    </Article>
  );
}
