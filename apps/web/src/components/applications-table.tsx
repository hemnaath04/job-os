"use client";

import { formatDistanceToNow } from "date-fns";
import { ExternalLink, Sparkles, Trash2 } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { CompanyAvatar } from "@/components/company-avatar";
import { StatusPill } from "@/components/status-pill";
import type { Application } from "@/lib/types";

export function ApplicationsTable({
  applications,
  onArchive,
  onRestore,
}: {
  applications: Application[];
  onArchive: (id: string) => Promise<unknown>;
  onRestore: (application: Application) => Promise<unknown>;
}) {
  function openJD(url: string | null | undefined) {
    if (!url) return;
    window.open(url, "_blank", "noopener,noreferrer");
  }

  async function onDelete(a: Application) {
    try {
      await onArchive(a.id);
      toast.success(`Archived "${a.job.title}"`, {
        description: a.job.company?.name ?? undefined,
        action: {
          label: "Undo",
          onClick: async () => {
            try {
              await onRestore(a);
            } catch (err) {
              toast.error(`Couldn't restore: ${(err as Error).message}`);
            }
          },
        },
      });
    } catch (err) {
      toast.error(`Couldn't archive: ${(err as Error).message}`);
    }
  }

  return (
    // Seven columns do not fit a phone. Scrolling the table keeps Status,
    // Location, Updated, Next action and the row actions reachable, where
    // overflow-hidden simply cut them off with no way to get to them.
    <div className="workspace-panel overflow-x-auto">
      {/* 44rem is the widest floor that still fits beside the sidebar at the
          lg breakpoint, so a laptop never scrolls and a phone never crushes. */}
      <table className="w-full min-w-[44rem] text-sm">
        <caption className="sr-only">
          Tracked applications, with status, location, and next action for each.
        </caption>
        <thead className="sticky top-0 z-10 bg-[color:var(--color-surface-1)]/80 text-left text-[10px] uppercase tracking-wider text-[color:var(--color-text-muted)] backdrop-blur-lg">
          <tr>
            <Th>Company</Th>
            <Th>Title</Th>
            <Th>Status</Th>
            <Th>Location</Th>
            <Th>Updated</Th>
            <Th>Next action</Th>
            <Th>Actions</Th>
          </tr>
        </thead>
        <tbody>
          {applications.map((a) => (
            <tr
              key={a.id}
              onDoubleClick={() => openJD(a.job.source_url)}
              title={a.job.source_url ? "Double-click to open the original JD" : undefined}
              className="cursor-default border-t border-[color:var(--color-border)] text-[color:var(--color-text)] transition hover:bg-[color:var(--color-surface-2)]"
            >
              <Td>
                <div className="flex items-center gap-2">
                  <CompanyAvatar name={a.job.company?.name ?? "Unknown"} domain={a.job.company?.domain} size={24} />
                  <span className="truncate">{a.job.company?.name ?? "Unknown"}</span>
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
              <Td className="text-[color:var(--color-text-muted)]">{a.job.location ?? "Not set"}</Td>
              <Td className="text-[color:var(--color-text-muted)]">
                {formatDistanceToNow(new Date(a.updated_at), { addSuffix: true })}
              </Td>
              <Td className="text-[color:var(--color-amber)]">
                {a.next_action_label ?? "Not set"}
              </Td>
              <Td>
                <div className="inline-flex items-center gap-2">
                  <Link
                    href={{ pathname: "/tailor", query: { job_id: a.job.id } }}
                    onClick={(e) => e.stopPropagation()}
                    onDoubleClick={(e) => e.stopPropagation()}
                    className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-2 py-1 text-xs font-semibold text-[color:var(--color-on-accent)] shadow-[0_10px_24px_-18px_rgba(233,198,74,.45)] transition hover:scale-[1.05]"
                  >
                    <Sparkles className="size-3" /> Tailor
                  </Link>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(a);
                    }}
                    onDoubleClick={(e) => e.stopPropagation()}
                    title="Archive application"
                    aria-label="Archive application"
                    className="inline-flex items-center justify-center rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-1 text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-rose)]/12 hover:text-[color:var(--color-rose-ink)]"
                  >
                    <Trash2 className="size-3" />
                  </button>
                </div>
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th scope="col" className="px-4 py-2.5 font-medium">
      {children}
    </th>
  );
}
function Td({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-4 py-3 ${className}`}>{children}</td>;
}
