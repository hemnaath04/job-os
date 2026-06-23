"use client";

import { formatDistanceToNow } from "date-fns";
import { ExternalLink, Sparkles } from "lucide-react";
import Link from "next/link";
import { CompanyAvatar } from "@/components/company-avatar";
import { StatusPill } from "@/components/status-pill";
import type { Application } from "@/lib/types";

export function ApplicationsTable({
  applications,
  onChange: _onChange,
}: {
  applications: Application[];
  onChange: () => void;
}) {
  function openJD(url: string | null | undefined) {
    if (!url) return;
    window.open(url, "_blank", "noopener,noreferrer");
  }

  return (
    <div className="glass overflow-hidden rounded-[var(--radius-card-lg)]">
      <table className="w-full text-sm">
        <thead className="sticky top-0 z-10 bg-[color:var(--color-surface-1)]/80 text-left text-[10px] uppercase tracking-wider text-[color:var(--color-text-dim)] backdrop-blur-lg">
          <tr>
            <Th>Company</Th>
            <Th>Title</Th>
            <Th>Status</Th>
            <Th>Location</Th>
            <Th>Updated</Th>
            <Th>Next action</Th>
            <Th>Tailor</Th>
          </tr>
        </thead>
        <tbody>
          {applications.map((a) => (
            <tr
              key={a.id}
              onDoubleClick={() => openJD(a.job.source_url)}
              title={a.job.source_url ? "Double-click to open the original JD" : undefined}
              className="cursor-default border-t border-[color:var(--color-border)] transition hover:bg-white/[0.025]"
            >
              <Td>
                <div className="flex items-center gap-2">
                  <CompanyAvatar name={a.job.company?.name ?? "Unknown"} size={24} />
                  <span className="truncate">{a.job.company?.name ?? "—"}</span>
                </div>
              </Td>
              <Td className="font-medium">
                <div className="inline-flex items-center gap-1.5">
                  <span>{a.job.title}</span>
                  {a.job.source_url && (
                    <a
                      href={a.job.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      onDoubleClick={(e) => e.stopPropagation()}
                      title="Open original JD"
                      className="text-[color:var(--color-text-dim)] transition hover:text-[color:var(--color-violet)]"
                      aria-label="Open original job description"
                    >
                      <ExternalLink className="size-3.5" />
                    </a>
                  )}
                </div>
              </Td>
              <Td>
                <StatusPill status={a.status} />
              </Td>
              <Td className="text-[color:var(--color-text-muted)]">
                {a.job.location ?? "—"}
              </Td>
              <Td className="text-[color:var(--color-text-dim)]">
                {formatDistanceToNow(new Date(a.updated_at), { addSuffix: true })}
              </Td>
              <Td className="text-[color:var(--color-amber)]">
                {a.next_action_label ?? "—"}
              </Td>
              <Td>
                <Link
                  href={{ pathname: "/tailor", query: { job_id: a.job.id } }}
                  onClick={(e) => e.stopPropagation()}
                  onDoubleClick={(e) => e.stopPropagation()}
                  className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-2 py-1 text-xs font-medium text-black shadow-[0_0_20px_-8px_var(--color-purple)] transition hover:scale-[1.05]"
                >
                  <Sparkles className="size-3" /> Tailor
                </Link>
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="px-4 py-2.5 font-medium">{children}</th>;
}
function Td({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-4 py-3 ${className}`}>{children}</td>;
}
