"use client";

import { useQuery } from "@tanstack/react-query";
import { LayoutGrid, Plus, Rows3 } from "lucide-react";
import { useState } from "react";
import { AddJobDialog } from "@/components/add-job-dialog";
import { KanbanBoard } from "@/components/kanban-board";
import { ApplicationsTable } from "@/components/applications-table";
import { api } from "@/lib/api";

export default function ApplicationsPage() {
  const [view, setView] = useState<"kanban" | "table">("kanban");
  const [open, setOpen] = useState(false);

  const { data: applications = [], refetch, isLoading } = useQuery({
    queryKey: ["applications"],
    queryFn: () => api.listApplications(),
  });

  return (
    <div className="mx-auto max-w-[1600px] px-8 py-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-medium tracking-tight">Applications</h1>
          <p className="text-sm text-[color:var(--color-text-muted)]">
            {applications.length} active · drag cards to update status
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-full border border-white/10 bg-white/[0.03] p-0.5">
            <ViewToggle active={view === "kanban"} onClick={() => setView("kanban")} icon={<LayoutGrid className="size-3.5" />} label="Kanban" />
            <ViewToggle active={view === "table"} onClick={() => setView("table")} icon={<Rows3 className="size-3.5" />} label="Table" />
          </div>
          <button
            onClick={() => setOpen(true)}
            className="flex items-center gap-1.5 rounded-full bg-[#7C5CFF] px-4 py-1.5 text-sm font-medium text-white shadow-[0_0_30px_-8px_#7C5CFF] hover:bg-[#8C6CFF]"
          >
            <Plus className="size-3.5" /> Add job
          </button>
        </div>
      </header>

      <div className="mt-6">
        {isLoading ? (
          <div className="text-sm text-[color:var(--color-text-muted)]">loading…</div>
        ) : view === "kanban" ? (
          <KanbanBoard applications={applications} onChange={() => refetch()} />
        ) : (
          <ApplicationsTable applications={applications} onChange={() => refetch()} />
        )}
      </div>

      <AddJobDialog open={open} onOpenChange={setOpen} onCreated={() => refetch()} />
    </div>
  );
}

function ViewToggle({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition ${
        active
          ? "bg-white/10 text-white"
          : "text-[color:var(--color-text-muted)] hover:text-white"
      }`}
    >
      {icon} {label}
    </button>
  );
}
