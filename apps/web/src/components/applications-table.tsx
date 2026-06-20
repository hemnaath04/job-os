"use client";

import { formatDistanceToNow } from "date-fns";
import type { Application } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

export function ApplicationsTable({
  applications,
  onChange: _onChange,
}: {
  applications: Application[];
  onChange: () => void;
}) {
  return (
    <div className="glass overflow-hidden rounded-[var(--radius-card)]">
      <table className="w-full text-sm">
        <thead className="bg-white/[0.02] text-left text-xs uppercase tracking-wide text-[color:var(--color-text-dim)]">
          <tr>
            <Th>Company</Th>
            <Th>Title</Th>
            <Th>Status</Th>
            <Th>Location</Th>
            <Th>Updated</Th>
            <Th>Next action</Th>
          </tr>
        </thead>
        <tbody>
          {applications.map((a) => (
            <tr
              key={a.id}
              className="border-t border-white/5 hover:bg-white/[0.02]"
            >
              <Td>{a.job.company?.name ?? "—"}</Td>
              <Td className="font-medium">{a.job.title}</Td>
              <Td>
                <span className="rounded-full bg-white/[0.04] px-2 py-0.5 text-xs">
                  {STATUS_LABELS[a.status]}
                </span>
              </Td>
              <Td className="text-[color:var(--color-text-muted)]">{a.job.location ?? "—"}</Td>
              <Td className="text-[color:var(--color-text-dim)]">
                {formatDistanceToNow(new Date(a.updated_at), { addSuffix: true })}
              </Td>
              <Td className="text-[color:var(--color-amber)]">
                {a.next_action_label ?? "—"}
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
