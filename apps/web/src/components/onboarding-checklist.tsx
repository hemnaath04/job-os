"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, ChevronRight, Circle } from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";

interface Step {
  key: string;
  label: string;
  description: string;
  href: string;
  done: boolean;
}

/**
 * The three things a first-time account needs before job.os can do its one
 * job: tailor a resume from something real. Skipping straight to Tailor or
 * Job Finder with none of this set up is where a new account actually gets
 * stuck, not a missing feature -- so this sits above everything else on the
 * dashboard until it is genuinely done, then disappears on its own. No
 * dismiss button: every check here is read from real data (facts and
 * resumes that exist), not a flag a user can tick to make the reminder go
 * away without doing the thing.
 */
export function OnboardingChecklist() {
  const { data: facts } = useQuery({ queryKey: ["facts"], queryFn: () => api.listFacts() });
  const { data: resumes } = useQuery({ queryKey: ["resumes"], queryFn: () => api.listResumes() });

  // Undetermined while either query is still loading -- showing nothing
  // beats a flash of "all done" that then reveals three unchecked steps.
  if (facts === undefined || resumes === undefined) return null;

  // Written for someone who has never used this, which is the only person who
  // ever sees it. The old wording named the machinery ("verified facts", "the
  // canonical resume", "a general-purpose identity") and gave "SWE, ML, AI" as
  // the examples of a role, which is a whole product's worth of assumption
  // about who is signing in. Each line now says what the user does and why,
  // in words that mean the same thing to a nurse and to a backend engineer.
  const steps: Step[] = [
    {
      key: "facts",
      label: "Add what you have done",
      description: "Your jobs, projects and skills. A tailored resume only writes from these.",
      href: "/profile",
      done: facts.length > 0,
    },
    {
      key: "master",
      label: "Upload your master resume",
      description: "The one resume every tailored version starts from.",
      href: "/resumes",
      done: resumes.some((r) => r.is_master),
    },
    {
      key: "source",
      label: "Add a second resume to build on",
      description: "A general version for one kind of role you apply to often.",
      href: "/resumes",
      done: resumes.some((r) => !r.is_master),
    },
  ];

  if (steps.every((s) => s.done)) return null;

  const doneCount = steps.filter((s) => s.done).length;

  return (
    <div className="mb-3.5 rounded-2xl border border-[color:var(--color-accent-border)] bg-[color:var(--color-accent-soft)]/40 p-4">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-[color:var(--color-text)]">
          Finish setting up job.os
        </h2>
        <span className="shrink-0 text-xs tabular-nums text-[color:var(--color-text-muted)]">
          {doneCount}/{steps.length}
        </span>
      </div>
      <p className="mt-1 text-xs text-[color:var(--color-text-muted)]">
        Everything this app writes for you comes from what you add here. Nothing is invented.
      </p>
      <ul className="mt-3 flex flex-col gap-0.5">
        {steps.map((step) => (
          <li key={step.key}>
            <Link
              href={step.href}
              className="group -mx-2 flex items-center gap-2.5 rounded-lg px-2 py-1.5 transition hover:bg-[color:var(--color-surface-hover)]"
            >
              {step.done ? (
                <CheckCircle2
                  className="size-4 shrink-0 text-[color:var(--color-accent-ink)]"
                  aria-hidden="true"
                />
              ) : (
                <Circle
                  className="size-4 shrink-0 text-[color:var(--color-text-dim)]"
                  aria-hidden="true"
                />
              )}
              <span className="flex-1 min-w-0">
                <span
                  className={
                    step.done
                      ? "text-sm text-[color:var(--color-text-muted)] line-through"
                      : "text-sm text-[color:var(--color-text)]"
                  }
                >
                  {step.label}
                </span>
                {!step.done && (
                  <span className="block truncate text-xs text-[color:var(--color-text-muted)]">
                    {step.description}
                  </span>
                )}
              </span>
              {!step.done && (
                <ChevronRight
                  className="size-4 shrink-0 text-[color:var(--color-text-dim)] opacity-0 transition group-hover:opacity-100"
                  aria-hidden="true"
                />
              )}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
