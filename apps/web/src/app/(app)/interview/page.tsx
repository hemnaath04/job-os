"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  Bookmark,
  CheckCircle2,
  Loader2,
  MessageSquareText,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useEffect, useState } from "react";
import { EmptyState } from "@/components/empty-state";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { Field } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";
import type {
  InterviewPrep,
  InterviewQuestion,
  QuestionCategory,
  QuestionConfidence,
  ReadinessReport,
} from "@/lib/types";

/**
 * Section order, and the promise each section makes.
 *
 * `resume_probe` sits directly under the readiness panel rather than last,
 * because it is the category nobody prepares for and the one whose questions are
 * certain to be asked: every one of them is about a sentence already on the page
 * the interviewer is holding.
 */
const SECTIONS: {
  category: QuestionCategory;
  label: string;
  blurb: string;
}[] = [
  {
    category: "resume_probe",
    label: "Questions about your own resume",
    blurb:
      "Written against the bullets on the resume for this role. These get asked, and almost nobody rehearses them.",
  },
  {
    category: "technical",
    label: "Technical",
    blurb: "Grounded in the skills this posting actually names, not a generic bank.",
  },
  {
    category: "behavioral",
    label: "Behavioural",
    blurb:
      "Aimed at the competencies the posting names, scaffolded only where your verified profile can carry the answer.",
  },
  {
    category: "candidate_ask",
    label: "Questions to ask them",
    blurb: "Specific to this role and this company as the posting describes them.",
  },
];

const CONFIDENCE_OPTIONS: { value: QuestionConfidence; label: string }[] = [
  { value: "shaky", label: "Shaky" },
  { value: "workable", label: "Workable" },
  { value: "solid", label: "Solid" },
];

const BAND_COPY: Record<string, string> = {
  strong: "Strong",
  mixed: "Mixed",
  thin: "Thin",
  not_scored: "Not scored",
};

export default function InterviewPrepPage() {
  const queryClient = useQueryClient();
  const [applicationId, setApplicationId] = useState("");

  const { data: applications = [], isLoading: loadingApplications } = useQuery({
    queryKey: ["applications"],
    queryFn: () => api.listApplications(),
  });

  // Preselect the first application so the page has something to show without a
  // click. Guarded on the current value rather than run once, because the list
  // arrives after the first render.
  useEffect(() => {
    if (!applicationId && applications.length > 0) {
      setApplicationId(applications[0].id);
    }
  }, [applicationId, applications]);

  const { data: prep, isLoading: loadingPrep } = useQuery({
    queryKey: ["interview-prep", applicationId],
    queryFn: () => api.latestInterviewPrep(applicationId),
    enabled: Boolean(applicationId),
  });

  const generate = useMutation({
    mutationFn: () => api.generateInterviewPrep(applicationId),
    onSuccess: (fresh) => {
      queryClient.setQueryData<InterviewPrep | null>(
        ["interview-prep", applicationId],
        fresh,
      );
    },
  });

  const practise = useMutation({
    mutationFn: ({
      questionId,
      patch,
    }: {
      questionId: string;
      patch: { flagged?: boolean; confidence?: QuestionConfidence };
    }) => api.patchInterviewQuestion(questionId, patch),
    onSuccess: (saved) => {
      queryClient.setQueryData<InterviewPrep | null>(
        ["interview-prep", applicationId],
        (current) =>
          current
            ? {
                ...current,
                questions: current.questions.map((question) =>
                  question.id === saved.id ? saved : question,
                ),
              }
            : current,
      );
    },
  });

  const selected = applications.find((application) => application.id === applicationId);
  const report = (prep?.readiness_report ?? {}) as Partial<ReadinessReport>;
  const gapCount = prep?.questions.filter((question) => question.gap).length ?? 0;
  const scaffoldCount =
    prep?.questions.filter((question) => question.scaffold !== null).length ?? 0;

  return (
    <div className="workspace-page">
      <PageIntro
        eyebrow="Interview prep"
        title="Interview Prep"
        description="Questions written from this posting and your own verified profile, with answer skeletons that cite the evidence behind them and gaps named as gaps."
        icon={MessageSquareText}
        action={
          <button
            onClick={() => generate.mutate()}
            disabled={!applicationId || generate.isPending}
            className="kinetic-button kinetic-button-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            {generate.isPending ? (
              <>
                <Loader2 className="size-3.5 animate-spin" /> Generating
              </>
            ) : (
              <>
                <Sparkles className="size-3.5" /> {prep ? "Regenerate" : "Generate pack"}
              </>
            )}
          </button>
        }
      >
        <InfoChip>{applications.length} roles tracked</InfoChip>
        {prep && <InfoChip tone="sage">{scaffoldCount} answers with evidence</InfoChip>}
        {prep && gapCount > 0 && <InfoChip tone="clay">{gapCount} gaps to prepare</InfoChip>}
      </PageIntro>

      <div className="mt-5 grid gap-4 lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)]">
        <div className="workspace-panel p-5">
          <Field
            label="Role"
            help="A pack is generated for one application, against the resume you tailored for it."
          >
            {(control) => (
              <Select
                {...control}
                value={applicationId}
                onChange={setApplicationId}
                disabled={loadingApplications || applications.length === 0}
                placeholder={
                  loadingApplications ? "Loading roles" : "No applications yet"
                }
                options={applications.map((application) => ({
                  value: application.id,
                  label: application.job.company?.name
                    ? `${application.job.title} at ${application.job.company.name}`
                    : application.job.title,
                }))}
              />
            )}
          </Field>
          {selected && (
            <p className="mt-4 text-xs leading-5 text-[color:var(--color-text-dim)]">
              Generating reads the parsed job description, the bullets of the resume you
              tailored for this role, and your verified profile. Nothing outside your
              verified profile can become an answer.
            </p>
          )}
        </div>

        {generate.isError ? (
          <div className="notice notice-critical flex items-start gap-2 p-5 text-sm">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>{(generate.error as Error).message}</span>
          </div>
        ) : loadingPrep ? (
          <div className="loading-surface" />
        ) : prep ? (
          <ReadinessPanel prep={prep} report={report} />
        ) : (
          <div className="workspace-panel flex items-center p-5 text-sm text-[color:var(--color-text-muted)]">
            No pack for this role yet. Generate one to see what they are likely to ask and
            which of it your profile can already answer.
          </div>
        )}
      </div>

      {prep ? (
        <div className="mt-4 space-y-4">
          {prep.note && (
            <div className="workspace-panel p-5 text-sm leading-6 text-[color:var(--color-text-muted)]">
              {prep.note}
            </div>
          )}

          <DefenceRisksPanel report={report} />

          {SECTIONS.map((section) => {
            const questions = prep.questions.filter(
              (question) => question.category === section.category,
            );
            if (questions.length === 0) return null;
            return (
              <section key={section.category} className="workspace-panel p-5">
                <h2 className="text-lg font-semibold tracking-tight">{section.label}</h2>
                <p className="mt-1 text-xs leading-5 text-[color:var(--color-text-dim)]">
                  {section.blurb}
                </p>
                <div className="mt-4 space-y-3">
                  {questions.map((question) => (
                    <QuestionCard
                      key={question.id}
                      question={question}
                      onPatch={(patch) =>
                        practise.mutate({ questionId: question.id, patch })
                      }
                    />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      ) : applications.length === 0 && !loadingApplications ? (
        <EmptyState
          icon={MessageSquareText}
          title="No applications to prepare for"
          description="Add a role and tailor a resume for it. The prep pack is built from that posting and your verified profile, so it needs both."
          cta={{ href: "/applications", label: "Add an application" }}
        />
      ) : null}
    </div>
  );
}

/**
 * The readiness number and everything needed to argue with it.
 *
 * The grade is the server's, derived from must-have coverage. The model's own
 * estimate is shown too, in muted text and explicitly labelled, because hiding
 * it would be tidier and would also mean the user cannot see the two disagree.
 * It never gets the large type.
 */
function ReadinessPanel({
  prep,
  report,
}: {
  prep: InterviewPrep;
  report: Partial<ReadinessReport>;
}) {
  const score = prep.readiness_score === null ? null : Number(prep.readiness_score);
  const band = report.band ?? "not_scored";
  const topics = report.topics ?? [];
  const evidenced = topics.filter((topic) => !topic.preferred && topic.status === "evidenced");
  const gaps = topics.filter((topic) => !topic.preferred && topic.status === "gap");
  const bonus = topics.filter((topic) => topic.preferred);

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="workspace-panel p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="section-kicker">Readiness</div>
          <div className="mt-1 flex items-baseline gap-2">
            <span className="text-4xl font-semibold tracking-[-0.04em]">
              {score === null ? "Not scored" : Math.round(score)}
            </span>
            {score !== null && (
              <span className="text-sm text-[color:var(--color-text-dim)]">/ 100</span>
            )}
            <span className="text-sm font-medium text-[color:var(--color-text-muted)]">
              {BAND_COPY[band] ?? band}
            </span>
          </div>
          <p className="mt-2 max-w-xl text-xs leading-5 text-[color:var(--color-text-dim)]">
            {report.formula}
          </p>
        </div>
        <div className="text-right">
          <div className="text-xs text-[color:var(--color-text-muted)]">
            {report.evidenced_topics ?? 0} of {report.scored_topics ?? 0} must-haves
            evidenced
          </div>
          {prep.model_estimate !== null && (
            <div className="mt-1 text-[11px] leading-4 text-[color:var(--color-text-dim)]">
              The model guessed {prep.model_estimate}. Kept for context only, it is not
              the grade.
            </div>
          )}
        </div>
      </div>

      {evidenced.length > 0 && (
        <TopicList
          label="You can speak to these"
          tone="evidenced"
          topics={evidenced.map((topic) => ({
            topic: topic.topic,
            detail: topic.citations.join("; "),
          }))}
        />
      )}
      {gaps.length > 0 && (
        <TopicList
          label="Nothing in your profile words these"
          tone="gap"
          topics={gaps.map((topic) => ({
            topic: topic.topic,
            detail:
              topic.alternatives.length > 1
                ? `Also looked for: ${topic.alternatives.join(", ")}`
                : "",
          }))}
        />
      )}
      {bonus.length > 0 && (
        <p className="mt-4 text-xs leading-5 text-[color:var(--color-text-dim)]">
          Nice to have, not counted in the number: {bonus.map((t) => t.topic).join(", ")}
        </p>
      )}
      {(report.unscored_requirements ?? []).length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs font-medium text-[color:var(--color-text-muted)]">
            Requirement sentences too long to match against one fact (
            {report.unscored_requirements?.length})
          </summary>
          <ul className="mt-2 space-y-1 text-xs leading-5 text-[color:var(--color-text-dim)]">
            {report.unscored_requirements?.map((sentence) => (
              <li key={sentence}>{sentence}</li>
            ))}
          </ul>
        </details>
      )}
    </motion.div>
  );
}

function TopicList({
  label,
  tone,
  topics,
}: {
  label: string;
  tone: "evidenced" | "gap";
  topics: { topic: string; detail: string }[];
}) {
  const Icon = tone === "evidenced" ? ShieldCheck : AlertTriangle;
  const color =
    tone === "evidenced" ? "var(--color-mint-ink)" : "var(--color-amber-ink)";
  return (
    <div className="mt-4">
      <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[color:var(--color-text-dim)]">
        <Icon className="size-3.5" style={{ color }} />
        {label}
      </div>
      <ul className="mt-2 space-y-1.5">
        {topics.map((entry) => (
          <li key={entry.topic} className="text-xs leading-5">
            <span className="font-medium">{entry.topic}</span>
            {entry.detail && (
              <span className="text-[color:var(--color-text-dim)]"> {entry.detail}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Claims already on the resume that the vault cannot currently back.
 *
 * Kept out of the readiness number and shown next to it, because "can you speak
 * to what they asked for" and "what on your page will you be asked to defend"
 * are two different questions and averaging them produces a number that answers
 * neither.
 */
function DefenceRisksPanel({ report }: { report: Partial<ReadinessReport> }) {
  const risks = report.defence_risks ?? [];
  if (risks.length === 0) return null;
  return (
    <section className="notice notice-caution p-5">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <AlertTriangle className="size-4" />
        On your resume, and not backed by your profile
      </h2>
      <ul className="mt-3 space-y-3">
        {risks.map((risk) => (
          <li key={risk.where}>
            <div className="text-sm leading-6">{risk.text}</div>
            <div className="notice-detail mt-1 text-xs leading-5">{risk.reason}</div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function QuestionCard({
  question,
  onPatch,
}: {
  question: InterviewQuestion;
  onPatch: (patch: { flagged?: boolean; confidence?: QuestionConfidence }) => void;
}) {
  return (
    <article className="rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="min-w-0 flex-1 text-sm font-medium leading-6">{question.question}</p>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          {question.topic && <InfoChip>{question.topic}</InfoChip>}
          {question.category !== "candidate_ask" && (
            <InfoChip tone={question.difficulty === "stretch" ? "clay" : "default"}>
              {question.difficulty}
            </InfoChip>
          )}
        </div>
      </div>

      {question.why_asked && (
        <p className="mt-2 text-xs leading-5 text-[color:var(--color-text-dim)]">
          {question.why_asked}
        </p>
      )}

      {question.scaffold && (
        <div className="mt-3 rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] p-3">
          <div className="section-kicker">Your answer, from your own evidence</div>
          <dl className="mt-2 space-y-2">
            <ScaffoldLine label="Situation" value={question.scaffold.situation} />
            <ScaffoldLine label="Task" value={question.scaffold.task} />
            <ScaffoldLine label="Action" value={question.scaffold.action} />
            <ScaffoldLine label="Result" value={question.scaffold.result} />
          </dl>
        </div>
      )}

      {question.evidence.length > 0 && (
        <div className="mt-3">
          <div className="section-kicker">Built from</div>
          <ul className="mt-1.5 space-y-1.5">
            {question.evidence.map((citation) => (
              <li
                key={`${citation.fact_id}-${citation.fact_bullet_id ?? "fact"}`}
                className="border-l-2 border-[color:var(--color-accent-border)] pl-2.5 text-xs leading-5 text-[color:var(--color-text-muted)]"
              >
                <span className="font-medium text-[color:var(--color-text)]">
                  {citation.label}
                </span>
                <span className="block">{citation.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {question.gap && question.gap_note && (
        <div className="notice notice-caution mt-3 flex items-start gap-2 p-3 text-xs leading-5">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{question.gap_note}</span>
        </div>
      )}

      {question.removed_claims.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs font-medium text-[color:var(--color-text-muted)]">
            {question.removed_claims.length} claim
            {question.removed_claims.length === 1 ? "" : "s"} removed, because your
            profile does not record them
          </summary>
          <ul className="mt-2 space-y-1 text-xs leading-5 text-[color:var(--color-text-dim)]">
            {question.removed_claims.map((claim) => (
              <li key={claim}>{claim}</li>
            ))}
          </ul>
        </details>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-[color:var(--color-border)] pt-3">
        <button
          onClick={() => onPatch({ flagged: !question.flagged })}
          aria-pressed={question.flagged}
          className={
            "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold transition " +
            (question.flagged
              ? "border-[color:var(--color-accent-border)] bg-[color:var(--color-accent-soft)] text-[color:var(--color-accent-ink)]"
              : "border-[color:var(--color-border)] text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]")
          }
        >
          <Bookmark className="size-3" />
          {question.flagged ? "Drilling" : "Drill this"}
        </button>
        <span className="ml-1 text-[11px] text-[color:var(--color-text-dim)]">
          How did that go?
        </span>
        {CONFIDENCE_OPTIONS.map((option) => (
          <button
            key={option.value}
            onClick={() => onPatch({ confidence: option.value })}
            aria-pressed={question.confidence === option.value}
            className={
              "rounded-full border px-2.5 py-1 text-[11px] font-semibold transition " +
              (question.confidence === option.value
                ? "border-[color:var(--color-accent-border)] bg-[color:var(--color-accent-soft)] text-[color:var(--color-accent-ink)]"
                : "border-[color:var(--color-border)] text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]")
            }
          >
            {option.label}
          </button>
        ))}
        {question.times_reviewed > 0 && (
          <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-[color:var(--color-text-dim)]">
            <CheckCircle2 className="size-3" />
            practised {question.times_reviewed}x
          </span>
        )}
      </div>
    </article>
  );
}

/** One STAR line. Empty fields are dropped rather than shown as a blank row. */
function ScaffoldLine({ label, value }: { label: string; value: string }) {
  if (!value.trim()) return null;
  return (
    <div className="flex gap-2">
      <dt className="w-16 shrink-0 text-[11px] font-semibold uppercase tracking-wide text-[color:var(--color-text-dim)]">
        {label}
      </dt>
      <dd className="min-w-0 flex-1 text-xs leading-5">{value}</dd>
    </div>
  );
}
