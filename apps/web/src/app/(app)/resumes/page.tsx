"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import {
  CheckCircle2,
  Crown,
  Download,
  ExternalLink,
  FileText,
  Plus,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { api } from "@/lib/api";
import { downloadPdf } from "@/lib/download";
import type { Resume, ResumeVersionSummary } from "@/lib/types";

export default function ResumesPage() {
  const qc = useQueryClient();
  const { data: resumes = [], isLoading } = useQuery({
    queryKey: ["resumes"],
    queryFn: () => api.listResumes(),
  });
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [baseRole, setBaseRole] = useState("");

  const create = useMutation({
    mutationFn: () =>
      api.createResume({
        name: name.trim(),
        base_role: baseRole.trim() || null,
        is_master: false,
      }),
    onSuccess: () => {
      toast.success("Template created");
      qc.invalidateQueries({ queryKey: ["resumes"] });
      setCreating(false);
      setName("");
      setBaseRole("");
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <div className="mx-auto max-w-5xl px-8 py-6">
      <header className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-medium tracking-tight">Resumes</h1>
          <p className="text-sm text-[color:var(--color-text-muted)]">
            Versions are rendered from your verified profile facts — every
            bullet is traceable.
          </p>
        </div>
        <button
          onClick={() => setCreating((c) => !c)}
          className="inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-4 py-1.5 text-sm font-semibold text-black shadow-[var(--shadow-brand-glow)] transition hover:scale-[1.02]"
        >
          <Plus className="size-3.5" /> New template
        </button>
      </header>

      {creating && (
        <div className="glass mt-4 rounded-[var(--radius-card)] border border-[color:var(--color-purple)]/30 p-4">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Template name (e.g. SWE, ML, AI)"
              autoFocus
              className="glass flex-1 rounded-[var(--radius-input,10px)] border border-white/10 bg-white/[0.03] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60"
            />
            <input
              type="text"
              value={baseRole}
              onChange={(e) => setBaseRole(e.target.value)}
              placeholder="Base role (optional)"
              className="glass rounded-[var(--radius-input,10px)] border border-white/10 bg-white/[0.03] px-3 py-2 text-sm outline-none focus:border-[#CCFF00]/60 sm:w-48"
            />
            <button
              onClick={() => create.mutate()}
              disabled={create.isPending || !name.trim()}
              className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-3 py-2 text-xs font-semibold text-black shadow-[var(--shadow-brand-glow)] transition enabled:hover:scale-[1.02] disabled:opacity-50"
            >
              {create.isPending ? "Creating…" : "Create"}
            </button>
            <button
              onClick={() => setCreating(false)}
              className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-[color:var(--color-text-muted)] hover:bg-white/[0.06]"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="mt-8 text-sm text-[color:var(--color-text-muted)]">
          loading…
        </div>
      )}

      {!isLoading && resumes.length === 0 && (
        <EmptyState
          icon={FileText}
          title="No resumes yet"
          description="Upload your master PDF on Profile — the Master resume is auto-created on import, and tailored versions land here as they're generated."
          cta={{ href: "/profile", label: "Open Profile" }}
        />
      )}

      <div className="mt-8 space-y-4">
        {resumes.map((r) => (
          <ResumeCard key={r.id} resume={r} />
        ))}
      </div>
    </div>
  );
}

function ResumeCard({ resume }: { resume: Resume }) {
  const { data: versions = [], isLoading } = useQuery({
    queryKey: ["versions", resume.id],
    queryFn: () => api.listVersions(resume.id),
  });

  return (
    <div className="glass overflow-hidden rounded-[var(--radius-card)]">
      <div className="flex items-center justify-between border-b border-white/[0.05] px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          {resume.is_master ? (
            <Crown className="size-4 text-[color:var(--color-amber)]" />
          ) : (
            <Sparkles className="size-4 text-[color:var(--color-violet)]" />
          )}
          <div>
            <div className="text-base font-semibold">
              {resume.name}
              {resume.is_master && (
                <span className="ml-2 rounded-full bg-[color:var(--color-amber)]/15 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[color:var(--color-amber)]">
                  master
                </span>
              )}
            </div>
            {resume.base_role && (
              <div className="text-xs text-[color:var(--color-text-muted)]">
                {resume.base_role}
              </div>
            )}
          </div>
        </div>
        <div className="text-xs text-[color:var(--color-text-dim)]">
          {versions.length} version{versions.length === 1 ? "" : "s"}
        </div>
      </div>

      <div className="divide-y divide-white/[0.04]">
        {isLoading && (
          <div className="px-5 py-3 text-sm text-[color:var(--color-text-muted)]">
            loading versions…
          </div>
        )}
        {!isLoading && versions.length === 0 && (
          <div className="px-5 py-3 text-sm text-[color:var(--color-text-muted)]">
            no versions yet
          </div>
        )}
        {versions.map((v) => (
          <VersionRow key={v.id} version={v} resumeId={resume.id} />
        ))}
      </div>
    </div>
  );
}

function VersionRow({
  version,
  resumeId,
}: {
  version: ResumeVersionSummary;
  resumeId: string;
}) {
  const downloadUrl = api.downloadVersionUrl(resumeId, version.id);
  const created = format(new Date(version.created_at), "MMM d, yyyy");
  return (
    <div className="flex items-center justify-between px-5 py-3 hover:bg-white/[0.02]">
      <div className="flex items-center gap-3">
        <FileText className="size-4 text-[color:var(--color-text-muted)]" />
        <div>
          <div className="text-sm font-medium">{created}</div>
          <div className="flex items-center gap-2 text-xs text-[color:var(--color-text-dim)]">
            {version.approved_by_user ? (
              <span className="inline-flex items-center gap-1 text-[color:var(--color-mint)]">
                <CheckCircle2 className="size-3" /> approved
              </span>
            ) : (
              <span>draft</span>
            )}
            {version.ats_score !== null && version.ats_score !== undefined && (
              <>
                <span>·</span>
                <span>ATS {version.ats_score}</span>
              </>
            )}
            {version.spawned_from_job_id && (
              <>
                <span>·</span>
                <span>tailored</span>
              </>
            )}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <a
          href={downloadUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs hover:bg-white/[0.06]"
        >
          <ExternalLink className="size-3" />
          Preview
        </a>
        <button
          onClick={() =>
            downloadPdf(downloadUrl, `resume_${version.id.slice(0, 8)}.pdf`)
          }
          className="inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-3 py-1.5 text-xs font-semibold text-black shadow-[var(--shadow-brand-glow)] transition hover:scale-[1.02]"
        >
          <Download className="size-3" />
          Download PDF
        </button>
      </div>
    </div>
  );
}
