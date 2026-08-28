"use client";

/**
 * Cover letters: pick a job, write the letter, read what backs every claim.
 *
 * Deliberately its own route rather than a panel inside /tailor. That page is
 * 1,813 lines and heavily coupled to the tailoring flow, and a letter is a
 * different document with a different contract, so it gets a page instead of a
 * tab in something already at its limit.
 *
 * The one idea this screen has to carry is that a claim and its proof are shown
 * together. A letter that says something the profile cannot support is the
 * failure this whole feature exists to prevent, so the evidence is not behind a
 * disclosure: the backed claims, the questions it refused to answer by inventing
 * an answer, and the sentences it deleted are all on the page next to the prose.
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  BadgeCheck,
  Download,
  FileSignature,
  HelpCircle,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { Field } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { api } from "@/lib/api";
import {
  coverLetters,
  explainRefusal,
  type CoverLetterTone,
  type CoverLetterVersion,
} from "@/lib/cover-letters";
import { downloadPdf } from "@/lib/download";
import { jobDisplay } from "@/lib/job-display";

const TONES: { value: CoverLetterTone; label: string }[] = [
  { value: "plain", label: "Plain, flat and factual" },
  { value: "warm", label: "Warm, same facts less clipped" },
  { value: "direct", label: "Direct, shortest that makes the case" },
];

/**
 * `?job_id=` support exists so a link from an application lands on this page
 * with its posting already chosen. A CTA that drops the user at an empty
 * picker and asks them to find the job they were just looking at is a step
 * backwards, not a shortcut.
 *
 * Suspense because useSearchParams opts the route into client rendering, and
 * Next requires the boundary to say what shows while it resolves.
 */
export default function CoverLettersPage() {
  return (
    <Suspense fallback={<div className="workspace-page max-w-6xl" />}>
      <CoverLettersView />
    </Suspense>
  );
}

function CoverLettersView() {
  const searchParams = useSearchParams();
  const [pickedJobId, setJobId] = useState(() => searchParams.get("job_id") ?? "");
  const [tone, setTone] = useState<CoverLetterTone>("plain");
  const [recipient, setRecipient] = useState("");
  const [version, setVersion] = useState<CoverLetterVersion | null>(null);

  const { data: jobs = [], isLoading: jobsLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.listJobs(),
  });
  // A letter is written from the master resume's latest version, so a vault
  // with no master cannot produce one. The backend says so with a 409, which
  // the user only sees after picking a job, filling in a tone and pressing a
  // button that was always going to fail. Asked for up front instead.
  const { data: resumes = [], isLoading: resumesLoading } = useQuery({
    queryKey: ["resumes"],
    queryFn: () => api.listResumes(),
  });

  const generate = useMutation({
    mutationFn: () =>
      coverLetters.generate({
        job_id: jobId,
        tone,
        recipient_name: recipient.trim() || null,
        // Regenerating records the version it came from as the parent, which is
        // what gives a letter history instead of one mutable draft.
        parent_version_id: version?.id ?? null,
      }),
    onSuccess: (next) => {
      setVersion(next);
      toast.success(
        `${next.document.word_count} words, ${next.provenance.length} backed claims`,
      );
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const approve = useMutation({
    mutationFn: (current: CoverLetterVersion) =>
      coverLetters.approve(current.cover_letter_id, current.id),
    onSuccess: (next) => {
      setVersion(next);
      toast.success("Marked as the letter you are sending.");
    },
    onError: (error: Error) => toast.error(error.message),
  });

  // The same reading /tailor's picker uses: a row whose import never finished
  // has no description to answer, so offering it as a choice only produces a
  // letter about nothing. Hidden here rather than shown and then rejected.
  const pickableJobs = jobs.filter((job) => !jobDisplay(job).incomplete);
  // A `?job_id=` for a row that is not offered (deleted since, or an import
  // that never finished reading) resolves to no selection rather than to a
  // Select showing a value none of its options carry.
  const jobId = pickableJobs.some((job) => job.id === pickedJobId) ? pickedJobId : "";
  const hasMaster = resumes.some((resume) => resume.is_master);
  const loading = jobsLoading || resumesLoading;
  // Everything standing between this person and a letter, in the order they
  // would fix it. Empty means the button works.
  const blockers = loading
    ? []
    : [
        !hasMaster && {
          what: "a master resume",
          why: "A letter is written from the same profile your resume is, so there has to be one to write from.",
          href: "/resumes",
          cta: "Add your resume",
        },
        pickableJobs.length === 0 && {
          what: "a saved job",
          why: "The letter answers one posting. Save a role from Job Finder, or paste one in.",
          href: "/jobs",
          cta: "Find a job",
        },
      ].filter((entry): entry is Exclude<typeof entry, false> => Boolean(entry));
  const ready = !loading && blockers.length === 0;

  const jobOptions = [
    {
      value: "",
      label: loading
        ? "Loading jobs…"
        : pickableJobs.length === 0
          ? "No saved jobs yet"
          : "Pick a job",
    },
    ...pickableJobs.map((job) => {
      const display = jobDisplay(job);
      return {
        value: job.id,
        label: [display.title, display.company].filter(Boolean).join(" at "),
      };
    }),
  ];

  return (
    <div className="workspace-page max-w-6xl">
      <PageIntro
        eyebrow="Written from verified evidence only"
        title="Cover letters"
        description="One page of prose where every specific claim traces back to a bullet you verified. Anything this job wants that your profile cannot support comes back as a question, never as a sentence."
        icon={FileSignature}
      >
        <InfoChip tone="sage">250 to 350 words</InfoChip>
        <InfoChip>One page, enforced</InfoChip>
        <InfoChip tone="clay">Matches your resume template</InfoChip>
      </PageIntro>

      {/* The button below was disabled with no reason given for anyone whose
          vault could not produce a letter yet, which reads as broken rather
          than as unfinished. Each blocker names what is missing and links to
          the page that fixes it. */}
      {blockers.length > 0 && (
        <section className="workspace-panel mt-7 p-6">
          <h2 className="text-sm font-medium">
            Two things make a letter: your profile, and one job to answer.
          </h2>
          <p className="mt-1 text-xs text-[color:var(--color-text-muted)]">
            {blockers.length === 1
              ? `You are missing ${blockers[0].what}.`
              : `You are missing ${blockers.map((b) => b.what).join(" and ")}.`}
          </p>
          <ul className="mt-4 space-y-3">
            {blockers.map((blocker) => (
              <li
                key={blocker.href}
                className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-3"
              >
                <p className="min-w-0 flex-1 text-xs leading-5 text-[color:var(--color-text-muted)]">
                  {blocker.why}
                </p>
                <Link
                  href={blocker.href}
                  className="kinetic-button kinetic-button-secondary shrink-0"
                >
                  {blocker.cta}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="workspace-panel mt-7 p-6">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Job" help="The posting this letter answers.">
            {(control) => (
              <Select
                {...control}
                value={jobId}
                onChange={setJobId}
                options={jobOptions}
                disabled={loading || pickableJobs.length === 0}
              />
            )}
          </Field>
          <Field label="Tone" help="None of them is enthusiastic. Each is a way of being plain.">
            {(control) => (
              <Select
                {...control}
                value={tone}
                onChange={(next) => setTone(next as CoverLetterTone)}
                options={TONES}
              />
            )}
          </Field>
          <Field
            label="Addressed to"
            help="Only what you type. Nothing here guesses at a hiring manager."
          >
            {(control) => (
              <input
                {...control}
                value={recipient}
                onChange={(event) => setRecipient(event.target.value)}
                placeholder="Optional"
                className="w-full rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)]"
              />
            )}
          </Field>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={!ready || !jobId || generate.isPending}
            title={
              ready
                ? undefined
                : "Add the missing pieces above and this turns on."
            }
            onClick={() => generate.mutate()}
            className="kinetic-button kinetic-button-primary disabled:opacity-50"
          >
            <Sparkles className="size-4" />
            {generate.isPending
              ? "Writing…"
              : version
                ? "Write another version"
                : "Write the letter"}
          </button>
          {version && (
            <>
              <button
                type="button"
                onClick={() =>
                  downloadPdf(
                    coverLetters.downloadUrl(version.cover_letter_id, version.id),
                    `cover-letter-${version.document.company || "letter"}.pdf`,
                  )
                }
                className="kinetic-button kinetic-button-secondary"
              >
                <Download className="size-4" />
                Download PDF
              </button>
              <button
                type="button"
                disabled={version.approved_by_user || approve.isPending}
                onClick={() => approve.mutate(version)}
                className="kinetic-button kinetic-button-secondary disabled:opacity-50"
              >
                <BadgeCheck className="size-4" />
                {version.approved_by_user ? "Approved" : "This is the one"}
              </button>
            </>
          )}
        </div>
        {generate.isPending && (
          <p className="mt-4 text-xs text-[color:var(--color-text-muted)]">
            Two passes over your verified profile. The second one only runs when
            there is something it could fix.
          </p>
        )}
      </section>

      {/* Only shown once a letter is actually possible. Telling someone with no
          resume and no saved job to "pick a job above" pointed at a control
          that had nothing in it, and the one link went to Profile whether or
          not Profile was the thing missing. The blockers panel above covers
          that case now, so this can say the one useful thing instead. */}
      {!version && !generate.isPending && ready && (
        <EmptyState
          icon={FileSignature}
          title="No letter yet"
          description="Pick a job above. The letter is assembled from bullets you have verified, so it can only say things you can defend in an interview."
          cta={{ href: "/profile", label: "Review what is verified" }}
        />
      )}

      {version && <LetterView version={version} />}
    </div>
  );
}

function LetterView({ version }: { version: CoverLetterVersion }) {
  const { document: letter } = version;
  // Rows keyed by paragraph, so a claim is shown against the paragraph it is in
  // rather than in a list the reader has to cross-reference by eye.
  const backing = new Map<number, typeof version.provenance>();
  for (const row of version.provenance) {
    backing.set(row.paragraph, [...(backing.get(row.paragraph) ?? []), row]);
  }
  const flags = Object.entries(version.quality_flags);

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="mt-7 grid gap-6 lg:grid-cols-[minmax(0,1.55fr)_minmax(0,1fr)]"
    >
      <article className="workspace-panel p-8">
        <header className="border-b border-[color:var(--color-border)] pb-5">
          <div className="text-base font-medium">{letter.sender.name}</div>
          <div className="mt-1 text-xs text-[color:var(--color-text-muted)]">
            {[letter.sender.location, letter.sender.email, letter.sender.phone]
              .filter(Boolean)
              .join("  |  ")}
          </div>
        </header>
        <div className="mt-5 text-xs text-[color:var(--color-text-muted)]">
          {letter.date}
        </div>
        {letter.subject && (
          <div className="mt-4 text-sm font-medium">{letter.subject}</div>
        )}
        <div className="mt-4 text-sm">{letter.greeting}</div>
        <div className="mt-4 space-y-4">
          {letter.paragraphs.map((paragraph, index) => (
            <div key={index}>
              <p className="text-sm leading-7">{paragraph}</p>
              {(backing.get(index)?.length ?? 0) > 0 && (
                <ul className="mt-2 space-y-1">
                  {backing.get(index)?.map((row) => (
                    <li
                      key={`${row.paragraph}-${row.sentence}`}
                      className="flex items-start gap-2 text-[11px] leading-5 text-[color:var(--color-text-muted)]"
                    >
                      <BadgeCheck className="mt-0.5 size-3 shrink-0 text-[color:var(--color-accent)]" />
                      <span>
                        Sentence {row.sentence + 1} is backed by bullet{" "}
                        <code className="font-mono">{row.fact_bullet_id}</code>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
        <div className="mt-6 text-sm">{letter.signoff}</div>
        <div className="mt-4 text-sm">{letter.sender.name}</div>
      </article>

      <aside className="space-y-5">
        <section className="workspace-panel p-5">
          <h2 className="section-kicker">This letter</h2>
          <dl className="mt-3 space-y-2 text-sm">
            <Stat label="Words" value={String(letter.word_count)} />
            <Stat label="Backed claims" value={String(version.provenance.length)} />
            <Stat label="Tone" value={version.tone} />
            <Stat label="Status" value={version.status} />
          </dl>
          {version.agent_note && (
            <p className="mt-4 text-xs leading-5 text-[color:var(--color-text-muted)]">
              {version.agent_note}
            </p>
          )}
        </section>

        {version.gap_questions.length > 0 && (
          <section className="workspace-panel p-5">
            <h2 className="section-kicker flex items-center gap-2">
              <HelpCircle className="size-3.5" />
              Questions instead of claims
            </h2>
            <p className="mt-2 text-xs leading-5 text-[color:var(--color-text-muted)]">
              This posting asks for these and your verified profile does not
              answer them, so the letter says nothing about them. Add the
              evidence to your profile and write it again.
            </p>
            <ul className="mt-3 space-y-2">
              {version.gap_questions.map((gap) => (
                <li key={gap.requirement} className="text-sm">
                  <span className="font-medium">{gap.requirement}</span>
                  <span className="block text-xs text-[color:var(--color-text-muted)]">
                    {gap.why_no_match}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {version.refused.length > 0 && (
          <section className="workspace-panel p-5">
            <h2 className="section-kicker flex items-center gap-2">
              <ShieldAlert className="size-3.5" />
              Sentences that did not print
            </h2>
            <p className="mt-2 text-xs leading-5 text-[color:var(--color-text-muted)]">
              Each of these claimed something your evidence does not carry, so it
              was deleted rather than softened.
            </p>
            <ul className="mt-3 space-y-3">
              {version.refused.map((refusal, index) => (
                <li key={index} className="text-xs leading-5">
                  <div className="text-[color:var(--color-text-muted)] line-through">
                    {refusal.text}
                  </div>
                  <div className="mt-1">{explainRefusal(refusal.reason)}</div>
                </li>
              ))}
            </ul>
          </section>
        )}

        {flags.length > 0 && (
          <section className="workspace-panel p-5">
            <h2 className="section-kicker">Writing notes</h2>
            <ul className="mt-3 space-y-1 text-xs text-[color:var(--color-text-muted)]">
              {flags.map(([where, values]) => (
                <li key={where}>
                  <span className="font-medium text-[color:var(--color-text)]">
                    {where}
                  </span>
                  : {values.join(", ")}
                </li>
              ))}
            </ul>
          </section>
        )}
      </aside>
    </motion.div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-[color:var(--color-text-muted)]">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}
