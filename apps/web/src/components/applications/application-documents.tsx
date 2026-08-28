"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileSignature, FileText, Sparkles, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import Link from "next/link";
import { buildResumeFilename, downloadPdf } from "@/lib/download";
import { api } from "@/lib/api";
import { appwriteWorkspace } from "@/lib/appwrite/workspace";
import { coverLetters } from "@/lib/cover-letters";
import type { Application } from "@/lib/types";

/**
 * The resume and cover letter tailored for this application, if any.
 *
 * Neither is a field on `Application` -- a resume container carries
 * `spawned_from_application_id`, and a cover letter version carries the same
 * on the version rather than the letter, so both are found by filtering the
 * lists already fetched elsewhere in the app rather than a new lookup field.
 * Only queried once an application is selected, not per row.
 */
export function ApplicationDocuments({ application }: { application: Application }) {
  const { data: resumes = [] } = useQuery({
    queryKey: ["resumes"],
    queryFn: () => api.listResumes(),
  });
  const linkedResume = resumes.find(
    (resume) => resume.spawned_from_application_id === application.id,
  );
  const { data: resumeVersions = [] } = useQuery({
    queryKey: ["versions", linkedResume?.id],
    queryFn: () => api.listVersions(linkedResume!.id),
    enabled: !!linkedResume,
  });
  const resumeVersion = resumeVersions[0];

  const { data: letters = [] } = useQuery({
    queryKey: ["cover-letters"],
    queryFn: () => coverLetters.list(),
  });
  const linkedLetter = letters.find((letter) => letter.job_id === application.job.id);
  const { data: letterVersions = [] } = useQuery({
    queryKey: ["cover-letter-versions", linkedLetter?.id],
    queryFn: () => coverLetters.listVersions(linkedLetter!.id),
    enabled: !!linkedLetter,
  });
  const letterVersion = letterVersions[0];
  // The one action that actually produces a document for this application --
  // pre-selects the job the same way the Job Finder's own Tailor button does,
  // so this is the shortest path from "I'm looking at an application with
  // nothing tailored yet" to a draft, not a second trip through the job picker.
  // application_id rides along so Tailor can link the resulting resume's
  // container back to this application (see api.tailorResume) -- without it,
  // a tailor run started from here would produce a real draft that never
  // shows up here afterward, which is the bug this wiring exists to close.
  const tailorHref = `/tailor?job_id=${application.job.id}&application_id=${application.id}`;
  // Carries the job the same way tailorHref does, so the letter page opens on
  // this posting instead of an empty picker. Without it the only route to a
  // cover letter from an application was the nav, and then choosing the job
  // again from a list -- which is why there was no letter CTA here at all.
  const letterHref = `/cover-letters?job_id=${application.job.id}`;

  if (!linkedResume && !linkedLetter) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-xs text-[color:var(--color-text-dim)]">
          No resume or cover letter for this application yet.
        </p>
        {/* Wraps rather than sitting in a row: at 390px two buttons side by
            side leave neither with a readable label. */}
        <div className="flex flex-wrap gap-2">
          <Link href={tailorHref} className="product-button product-button-primary">
            <Sparkles className="size-3.5" /> Tailor a resume
          </Link>
          <AttachResume application={application} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      {linkedResume && resumeVersion && (
        <DocumentRow
          label="Resume"
          name={linkedResume.name}
          onDownload={
            resumeVersion.status === "final"
              ? () =>
                  downloadPdf(
                    api.downloadVersionUrl(linkedResume.id, resumeVersion.id),
                    resumeVersion.source_filename ??
                      buildResumeFilename({ company: application.job.company?.name }),
                  )
              : undefined
          }
        />
      )}
      {linkedLetter && letterVersion && (
        <DocumentRow
          label="Cover letter"
          name={linkedLetter.name}
          // Was hardcoded to undefined, so the letter was listed as a document
          // of this application with no way to open it. The bytes have always
          // been there: the same route the Cover Letters page downloads from.
          onDownload={() =>
            downloadPdf(
              coverLetters.downloadUrl(linkedLetter.id, letterVersion.id),
              `cover-letter-${application.job.company?.name || "letter"}.pdf`,
            )
          }
        />
      )}
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link
          href={tailorHref}
          className="inline-flex w-fit items-center gap-1 text-[11px] text-[color:var(--color-violet)] hover:underline"
        >
          <Sparkles className="size-3" /> Tailor another version
        </Link>
        {/* The next step after a tailored resume, which this panel never
            offered. Only shown once there IS a resume: a letter is written
            from the same profile, so suggesting it first would just be a
            second way to reach the same missing prerequisites. */}
        {linkedResume && (
          <Link
            href={letterHref}
            className="inline-flex w-fit items-center gap-1 text-[11px] text-[color:var(--color-violet)] hover:underline"
          >
            <FileSignature className="size-3" />
            {linkedLetter ? "Write another letter" : "Write a cover letter"}
          </Link>
        )}
      </div>
      {/* Also offered once something is here: a tailored draft and the file
          that was actually sent are different documents, and an application
          often ends up carrying both. */}
      <div className="pt-1">
        <AttachResume application={application} />
      </div>
    </div>
  );
}

function DocumentRow({
  label,
  name,
  onDownload,
}: {
  label: string;
  name: string;
  onDownload?: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-lg bg-[color:var(--color-surface-2)] px-3 py-2 text-sm">
      <div className="flex min-w-0 items-center gap-2">
        <FileText className="size-3.5 shrink-0 text-[color:var(--color-text-dim)]" />
        <span className="min-w-0 truncate">
          <span className="text-[color:var(--color-text-dim)]">{label}</span> — {name}
        </span>
      </div>
      {onDownload && (
        <button
          type="button"
          onClick={onDownload}
          aria-label={`Download ${label.toLowerCase()}`}
          title={`Download ${label.toLowerCase()}`}
          className="shrink-0 rounded-full p-1.5 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)]"
        >
          <Download className="size-3.5" />
        </button>
      )}
    </div>
  );
}


/**
 * Keep a resume written elsewhere against this application.
 *
 * Stored as the file it is rather than imported: a resume already tailored
 * somewhere else is finished work, and parsing it back into a JSON Resume
 * would invite the page to rebuild something the person is done with.
 */
function AttachResume({ application }: { application: Application }) {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const attach = useMutation({
    mutationFn: (file: File) =>
      appwriteWorkspace.attachResumeToApplication(file, {
        applicationId: application.id,
        name:
          [application.job.company?.name, application.job.title]
            .filter(Boolean)
            .join(" - ") || file.name.replace(/\.[^.]+$/, ""),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["resumes"] });
      toast.success("Resume attached to this application.");
    },
    onError: (error) =>
      toast.error(error instanceof Error ? error.message : "Could not attach that file."),
    onSettled: () => setBusy(false),
  });

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.docx"
        className="sr-only"
        onChange={(event) => {
          const file = event.target.files?.[0];
          // Cleared before the upload starts, so picking the same file twice
          // in a row still fires a change event the second time.
          event.target.value = "";
          if (!file) return;
          setBusy(true);
          attach.mutate(file);
        }}
      />
      <button
        type="button"
        disabled={busy}
        onClick={() => inputRef.current?.click()}
        className="product-button disabled:opacity-60"
      >
        <Upload className="size-3.5" />
        {busy ? "Attaching..." : "Attach a resume"}
      </button>
    </>
  );
}

