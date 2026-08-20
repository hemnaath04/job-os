"use client";

import { useQuery } from "@tanstack/react-query";
import { Download, FileText } from "lucide-react";
import { buildResumeFilename, downloadPdf } from "@/lib/download";
import { api } from "@/lib/api";
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

  if (!linkedResume && !linkedLetter) {
    return (
      <p className="text-xs text-[color:var(--color-text-dim)]">
        No resume or cover letter tailored for this application yet.
      </p>
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
                      buildResumeFilename([application.job.company?.name, application.job.title]),
                  )
              : undefined
          }
        />
      )}
      {linkedLetter && letterVersion && (
        <DocumentRow
          label="Cover letter"
          name={linkedLetter.name}
          onDownload={undefined}
        />
      )}
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
