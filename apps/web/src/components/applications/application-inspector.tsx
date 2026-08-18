"use client";

import { format } from "date-fns";
import {
  ArrowLeft,
  Archive,
  Check,
  ExternalLink,
  MoreHorizontal,
  StickyNote,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ApplicationDocuments } from "@/components/applications/application-documents";
import { ApplicationTimeline } from "@/components/applications/application-timeline";
import { CompanyAvatar } from "@/components/company-avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { MatchScoreChip, MatchScoreMeter, SkillChip, scoreLabel } from "@/components/ui/match-score";
import { Select } from "@/components/ui/select";
import { reportFailure } from "@/lib/errors";
import type { ProfileVocab } from "@/lib/discover/fit-score";
import { scoreApplicationJob } from "@/lib/discover/job-fit";
import type { Application, AppStatus } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

const STATUS_OPTIONS = (Object.keys(STATUS_LABELS) as AppStatus[]).map((value) => ({
  value,
  label: STATUS_LABELS[value],
}));

function formatSalary(job: Application["job"]): string | null {
  if (!job.salary_min && !job.salary_max) return null;
  const currency = job.salary_currency ?? "USD";
  const fmt = (n: number) => `${currency} ${n.toLocaleString()}`;
  if (job.salary_min && job.salary_max && job.salary_min !== job.salary_max) {
    return `${fmt(job.salary_min)} to ${fmt(job.salary_max)}`;
  }
  return fmt(job.salary_min ?? job.salary_max ?? 0);
}

export function ApplicationInspector({
  application,
  vocab,
  onPatch,
  onArchive,
  onRestore,
  onClose,
}: {
  application: Application;
  vocab: ProfileVocab;
  onPatch: (id: string, patch: Partial<Application>) => Promise<unknown>;
  onArchive: (id: string) => Promise<unknown>;
  onRestore: (application: Application) => Promise<unknown>;
  onClose?: () => void;
}) {
  const { job } = application;
  const fit = scoreApplicationJob(job, vocab);
  const [notes, setNotes] = useState(application.notes ?? "");
  const [nextActionLabel, setNextActionLabel] = useState(application.next_action_label ?? "");
  const [nextActionDate, setNextActionDate] = useState(
    application.next_action_at ? application.next_action_at.slice(0, 10) : "",
  );
  const notesRef = useRef<HTMLTextAreaElement>(null);
  const nextActionRef = useRef<HTMLInputElement>(null);

  // The inspector's own draft state has to resync when the selection changes
  // underneath it, since it is one mounted panel reused across every row
  // rather than one instance per application.
  useEffect(() => {
    setNotes(application.notes ?? "");
    setNextActionLabel(application.next_action_label ?? "");
    setNextActionDate(application.next_action_at ? application.next_action_at.slice(0, 10) : "");
  }, [application.id, application.notes, application.next_action_label, application.next_action_at]);

  async function saveNotes() {
    if (notes === (application.notes ?? "")) return;
    try {
      await onPatch(application.id, { notes: notes.trim() || null });
      toast.success("Note saved");
    } catch (err) {
      reportFailure("save that note", err);
    }
  }

  async function saveNextAction() {
    const label = nextActionLabel.trim() || null;
    const at = nextActionDate ? new Date(nextActionDate).toISOString() : null;
    if (label === (application.next_action_label ?? null) && at === (application.next_action_at ?? null)) {
      return;
    }
    try {
      await onPatch(application.id, { next_action_label: label, next_action_at: at });
      toast.success("Next action saved");
    } catch (err) {
      reportFailure("save the next action", err);
    }
  }

  async function completeNextAction() {
    setNextActionLabel("");
    setNextActionDate("");
    try {
      await onPatch(application.id, { next_action_label: null, next_action_at: null });
      toast.success("Marked complete");
    } catch (err) {
      reportFailure("complete the next action", err);
    }
  }

  async function onArchiveClick() {
    try {
      await onArchive(application.id);
      toast.success(`Archived "${job.title}"`, {
        description: job.company?.name ?? undefined,
        action: { label: "Undo", onClick: () => onRestore(application) },
      });
    } catch (err) {
      reportFailure("archive that application", err);
    }
  }

  const salary = formatSalary(job);
  const hasNextAction = Boolean(application.next_action_label);

  return (
    // min-w-0 flex-1, not just flex flex-col: the parent wrapper in the page
    // is `lg:flex`, a row-direction flex container, so this root is a flex
    // item. Without flex-1 it takes `flex: 0 1 auto` and sizes to its own
    // content -- which is what left the inspector rendering at about 400px
    // inside an 820px grid cell and the right half of the panel empty. min-w-0
    // so long unbroken values can shrink it rather than push it wider.
    <div className="flex h-full min-h-0 min-w-0 flex-1 flex-col">
      {/* Sticky identity. Scrolling a long inspector should never leave you
          unsure which application you are editing. */}
      <div className="sticky top-0 z-10 shrink-0 border-b border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] px-4 py-3">
        <div className="flex items-start gap-2.5">
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Back to the list"
              className="mt-0.5 shrink-0 rounded-md p-1 text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-2)] lg:hidden"
            >
              <ArrowLeft className="size-4" />
            </button>
          )}
          <CompanyAvatar name={job.company?.name ?? "Unknown"} domain={job.company?.domain} size={32} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold leading-tight text-[color:var(--color-text)]">
              {job.company?.name ?? "Unknown company"}
            </div>
            <div className="truncate text-xs leading-tight text-[color:var(--color-text-muted)]">
              {job.title}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {job.source_url && (
              <a
                href={job.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 rounded-lg border border-[color:var(--color-border)] px-2 py-1 text-[11px] text-[color:var(--color-text-muted)] transition-colors hover:border-[color:var(--color-border-strong)] hover:text-[color:var(--color-text)]"
              >
                Open job <ExternalLink className="size-3" />
              </a>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  aria-label="Application actions"
                  className="rounded-lg p-1.5 text-[color:var(--color-text-dim)] transition-colors hover:bg-[color:var(--color-surface-2)] hover:text-[color:var(--color-text)] data-[state=open]:bg-[color:var(--color-surface-2)]"
                >
                  <MoreHorizontal className="size-4" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                {job.source_url && (
                  <DropdownMenuItem
                    icon={<ExternalLink className="size-3.5" />}
                    onSelect={() => window.open(job.source_url!, "_blank", "noopener,noreferrer")}
                  >
                    Open job posting
                  </DropdownMenuItem>
                )}
                <DropdownMenuItem
                  icon={<StickyNote className="size-3.5" />}
                  onSelect={() => setTimeout(() => notesRef.current?.focus(), 0)}
                >
                  Add note
                </DropdownMenuItem>
                <DropdownMenuItem
                  icon={<Check className="size-3.5" />}
                  onSelect={() => setTimeout(() => nextActionRef.current?.focus(), 0)}
                >
                  Set next action
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  destructive
                  icon={<Archive className="size-3.5" />}
                  onSelect={onArchiveClick}
                >
                  Archive
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

        <div className="mt-2.5 flex items-center gap-2">
          <Select
            compact
            value={application.status}
            onChange={(value) => onPatch(application.id, { status: value as AppStatus })}
            options={STATUS_OPTIONS}
            aria-label="Application stage"
            className="!w-36"
          />
          {fit.confident && (
            <div className="flex items-center gap-1.5">
              <MatchScoreChip score={fit.score} />
              <span className="text-[11px] text-[color:var(--color-text-dim)]">
                {scoreLabel(fit.score)}
              </span>
            </div>
          )}
        </div>
      </div>

      {/* @container, not a viewport breakpoint: this panel's width is a
          fraction of the master-detail split, so it can be narrow on a wide
          screen (and the mobile full-screen case is the opposite). Asking the
          viewport how wide this panel is would be asking the wrong element. */}
      <div className="@container min-h-0 flex-1 overflow-y-auto px-4 py-3.5">
        <div className="grid grid-cols-1 gap-x-8 gap-y-5 @2xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
          {/* Left: the things you read, then the thing you do next. */}
          <div className="flex min-w-0 flex-col gap-5">
            <Section title="Overview">
              <dl className="grid grid-cols-[104px_minmax(0,1fr)] gap-x-4 gap-y-2 text-xs">
                <DetailRow label="Location" value={job.location} />
                <DetailRow label="Work type" value={job.remote} />
                <DetailRow label="Salary" value={salary} />
                <DetailRow label="Job type" value={job.level} />
                <DetailRow label="Source" value={job.source} />
                <DetailRow
                  label="Applied"
                  value={
                    application.applied_at
                      ? format(new Date(application.applied_at), "MMM d, yyyy")
                      : null
                  }
                />
                <DetailRow label="Recruiter" value={application.recruiter_name} />
              </dl>
            </Section>

            <Section title="Next action">
              <div className="flex flex-col gap-1.5">
                <input
                  ref={nextActionRef}
                  type="text"
                  value={nextActionLabel}
                  onChange={(event) => setNextActionLabel(event.target.value)}
                  onBlur={saveNextAction}
                  placeholder="e.g. Prepare technical interview"
                  className="field-control !min-h-8 !py-1.5 !text-xs"
                />
                <div className="flex items-center gap-1.5">
                  <input
                    type="date"
                    value={nextActionDate}
                    onChange={(event) => setNextActionDate(event.target.value)}
                    onBlur={saveNextAction}
                    className="field-control !min-h-8 min-w-0 flex-1 !py-1.5 !text-xs"
                  />
                  {hasNextAction && (
                    <button
                      type="button"
                      onClick={completeNextAction}
                      className="flex shrink-0 items-center gap-1 rounded-lg border border-[color:var(--color-border)] px-2 py-1.5 text-[11px] text-[color:var(--color-text-muted)] transition-colors hover:border-[color:var(--color-accent-border)] hover:text-[color:var(--color-accent-ink)]"
                    >
                      <Check className="size-3" /> Complete
                    </button>
                  )}
                </div>
              </div>
            </Section>
          </div>

          {/* Right: the assessment, the history, the artifacts. */}
          <div className="flex min-w-0 flex-col gap-5">
        <Section title="AI match">
          {fit.confident ? (
            <>
              <MatchScoreMeter
                score={fit.score}
                matched={fit.matched.length}
                total={fit.matched.length + fit.gaps.length}
              />
              {/* The score was always computed from these two lists, but only
                  the number was ever shown -- which makes a 47% unactionable:
                  you cannot tell whether the gap is one missing framework or a
                  whole discipline. */}
              {(fit.matched.length > 0 || fit.gaps.length > 0) && (
                <div className="mt-3 flex flex-col gap-2.5">
                  {fit.matched.length > 0 && (
                    <SkillGroup label="You have" skills={fit.matched} matched />
                  )}
                  {fit.gaps.length > 0 && (
                    <SkillGroup label="Not on your profile" skills={fit.gaps} matched={false} />
                  )}
                </div>
              )}
            </>
          ) : (
            <div className="rounded-lg border border-dashed border-[color:var(--color-border)] px-3 py-2.5">
              <p className="text-xs font-medium text-[color:var(--color-text-muted)]">
                Match unavailable
              </p>
              <p className="mt-0.5 text-[11px] leading-relaxed text-[color:var(--color-text-dim)]">
                This posting names too few recognizable skills to score reliably.
              </p>
            </div>
          )}
        </Section>

            <Section title="Timeline">
              <ApplicationTimeline application={application} />
            </Section>

            <Section title="Documents">
              <ApplicationDocuments application={application} />
            </Section>
          </div>

          {/* Notes spans both columns: a note is prose, and prose in a
              0.85fr column is a column of two-word lines. */}
          <div className="min-w-0 border-t border-[color:var(--color-border)] pt-4 @2xl:col-span-2">
            <Section title="Notes">
              <textarea
                ref={notesRef}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                onBlur={saveNotes}
                placeholder="Add a note..."
                rows={3}
                className="field-control min-h-20 resize-y !text-xs"
              />
            </Section>
          </div>
        </div>
      </div>
    </div>
  );
}

function SkillGroup({
  label,
  skills,
  matched,
}: {
  label: string;
  skills: string[];
  matched: boolean;
}) {
  return (
    <div>
      <div className="text-[10px] font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
        {label}
      </div>
      <div className="mt-1 flex flex-wrap gap-1">
        {skills.map((skill) => (
          <SkillChip key={skill} label={skill} matched={matched} />
        ))}
      </div>
    </div>
  );
}

/**
 * Sections carry no padding or rule of their own now. In a two-column grid a
 * per-section bottom border draws ragged lines that stop at different heights
 * in each column; the grid's own gap separates them more calmly, and the one
 * rule that remains (above Notes) spans the full width where it can land
 * straight.
 */
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="min-w-0">
      <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-[color:var(--color-text-dim)]">
        {title}
      </h3>
      {children}
    </section>
  );
}

/**
 * Left-aligned against a fixed label column, not pushed to opposite edges.
 * `justify-between` on a wide panel strands the value against the far margin
 * with a river of empty space between it and its own label, which is exactly
 * as hard to read as it sounds.
 */
function DetailRow({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <>
      <dt className="text-[color:var(--color-text-dim)]">{label}</dt>
      <dd className="min-w-0 break-words text-[color:var(--color-text)]">{value || "Not set"}</dd>
    </>
  );
}
