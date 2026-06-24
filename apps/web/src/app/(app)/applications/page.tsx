"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { Inbox, LayoutGrid, Plus, Rows3 } from "lucide-react";
import { useState } from "react";
import { AddJobDialog } from "@/components/add-job-dialog";
import { ApplicationsTable } from "@/components/applications-table";
import { EmptyState } from "@/components/empty-state";
import { KanbanBoard } from "@/components/kanban-board";
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
      <motion.header
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25 }}
        className="flex flex-wrap items-end justify-between gap-3"
      >
        <div>
          <h1 className="text-2xl font-medium tracking-tight">Applications</h1>
          <p className="text-sm text-[color:var(--color-text-muted)]">
            {applications.length} tracked · drag cards to update status
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-full border border-[color:var(--color-border)] bg-white/[0.03] p-0.5">
            <ViewToggle
              active={view === "kanban"}
              onClick={() => setView("kanban")}
              icon={<LayoutGrid className="size-3.5" />}
              label="Kanban"
            />
            <ViewToggle
              active={view === "table"}
              onClick={() => setView("table")}
              icon={<Rows3 className="size-3.5" />}
              label="Table"
            />
          </div>
          <button
            onClick={() => setOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-4 py-1.5 text-sm font-bold text-black shadow-[var(--shadow-brand-glow)] transition hover:scale-[1.02]"
          >
            <Plus className="size-3.5" /> Add job
          </button>
        </div>
      </motion.header>

      <div className="mt-6">
        {isLoading ? (
          <div className="text-sm text-[color:var(--color-text-muted)]">loading…</div>
        ) : applications.length === 0 ? (
          <EmptyState
            icon={Inbox}
            title="No applications yet"
            description="Add a job from a URL and it'll show up here as a card. Track status, set follow-ups, and tailor resumes per role."
            cta={{ href: "/jobs", label: "Find internships" }}
          />
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
      className={
        "relative flex items-center gap-1.5 rounded-full px-3 py-1 text-xs transition " +
        (active
          ? "font-bold text-black"
          : "font-medium text-[color:var(--color-text-muted)] hover:text-white")
      }
    >
      {active && (
        <motion.span
          layoutId="view-active"
          className="absolute inset-0 rounded-full bg-gradient-brand"
          transition={{ type: "spring", stiffness: 400, damping: 28 }}
        />
      )}
      <span className="relative inline-flex items-center gap-1.5">
        {icon} {label}
      </span>
    </button>
  );
}
