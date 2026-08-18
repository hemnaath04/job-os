"use client";

import { format } from "date-fns";
import { ArrowLeft, ExternalLink, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ApplicationDocuments } from "@/components/applications/application-documents";
import { ApplicationTimeline } from "@/components/applications/application-timeline";
import { CompanyAvatar } from "@/components/company-avatar";
import { Select } from "@/components/ui/select";
import { reportFailure } from "@/lib/errors";
import type { ProfileVocab } from "@/lib/discover/fit-score";
import { scoreApplicationJob } from "@/lib/discover/job-fit";
import { MatchScoreMeter, SkillChip } from "@/components/ui/match-score";
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

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="flex items-start gap-3 border-b border-[color:var(--color-border)] px-5 py-4">
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Back to the list"
            className="mt-1 shrink-0 rounded-full p-1.5 text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-2)] lg:hidden"
          >
            <ArrowLeft className="size-4" />
          </button>
        )}
        <CompanyAvatar name={job.company?.name ?? "Unknown"} domain={job.company?.domain} size={40} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-base font-semibold text-[color:var(--color-text)]">
            {job.company?.name ?? "Unknown company"}
          </div>
          <div className="truncate text-sm text-[color:var(--color-text-muted)]">{job.title}</div>
        </div>
        <button
          type="button"
          onClick={onArchiveClick}
          aria-label="Archive application"
          title="Archive application"
          className="shrink-0 rounded-full p-1.5 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-rose)]/12 hover:text-[color:var(--color-rose-ink)]"
        >
          <Trash2 className="size-4" />
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 px-5 py-3">
        {job.source_url && (
          <a
            href={job.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="kinetic-button kinetic-button-secondary"
          >
            Open job <ExternalLink className="size-3" />
          </a>
        )}
        <Select
          value={application.status}
          onChange={(value) => onPatch(application.id, { status: value as AppStatus })}
          options={STATUS_OPTIONS}
          aria-label="Application stage"
          className="w-40"
        />
      </div>

      <Section title="Application details">
        <DetailRow label="Location" value={job.location} />
        <DetailRow label="Work type" value={job.remote} />
        <DetailRow label="Salary" value={salary} />
        <DetailRow label="Job type" value={job.level} />
        <DetailRow
          label="Applied"
          value={application.applied_at ? format(new Date(application.applied_at), "MMM d, yyyy") : null}
        />
        <DetailRow label="Source" value={job.source} />
        <DetailRow label="Recruiter" value={application.recruiter_name} />
      </Section>

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
                whole discipline. Naming them turns the score into something
                you can do something about, and it is data we already have. */}
            {(fit.matched.length > 0 || fit.gaps.length > 0) && (
              <div className="mt-3 flex flex-col gap-2.5">
                {fit.matched.length > 0 && (
                  <div>
                    <div className="text-[10px] font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
                      You have
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {fit.matched.map((skill) => (
                        <SkillChip key={skill} label={skill} matched />
                      ))}
                    </div>
                  </div>
                )}
                {fit.gaps.length > 0 && (
                  <div>
                    <div className="text-[10px] font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
                      Not on your profile
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {fit.gaps.map((skill) => (
                        <SkillChip key={skill} label={skill} matched={false} />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        ) : (
          <p className="text-xs text-[color:var(--color-text-dim)]">
            Not enough parsed job data to score this one.
          </p>
        )}
      </Section>

      <Section title="Timeline">
        <ApplicationTimeline application={application} />
      </Section>

      <Section title="Next action">
        <div className="flex flex-col gap-2">
          <input
            type="text"
            value={nextActionLabel}
            onChange={(event) => setNextActionLabel(event.target.value)}
            onBlur={saveNextAction}
            placeholder="e.g. Technical interview"
            className="field-control"
          />
          <input
            type="date"
            value={nextActionDate}
            onChange={(event) => setNextActionDate(event.target.value)}
            onBlur={saveNextAction}
            className="field-control"
          />
        </div>
      </Section>

      <Section title="Documents">
        <ApplicationDocuments application={application} />
      </Section>

      <Section title="Notes" last>
        <textarea
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          onBlur={saveNotes}
          placeholder="Add a note..."
          rows={4}
          className="field-control min-h-24 resize-y"
        />
      </Section>
    </div>
  );
}

function Section({
  title,
  children,
  last = false,
}: {
  title: string;
  children: React.ReactNode;
  last?: boolean;
}) {
  return (
    <div
      className={
        "px-5 py-4" + (last ? "" : " border-b border-[color:var(--color-border)]")
      }
    >
      <h3 className="mb-2.5 text-xs font-semibold uppercase tracking-wide text-[color:var(--color-text-dim)]">
        {title}
      </h3>
      {children}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1 text-sm">
      <span className="text-[color:var(--color-text-dim)]">{label}</span>
      <span className="truncate text-[color:var(--color-text)]">{value || "Not set"}</span>
    </div>
  );
}
