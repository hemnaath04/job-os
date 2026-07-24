"use client";

import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { BriefcaseBusiness, Inbox, LayoutGrid, Plus, Rows3 } from "lucide-react";
import { useState } from "react";
import { AddJobDialog } from "@/components/add-job-dialog";
import { ApplicationsTable } from "@/components/applications-table";
import { EmptyState } from "@/components/empty-state";
import { KanbanBoard } from "@/components/kanban-board";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { api } from "@/lib/api";

export default function ApplicationsPage() {
  const [view, setView] = useState<"kanban" | "table">("kanban");
  const [open, setOpen] = useState(false);

  const { data: applications = [], refetch, isLoading } = useQuery({
    queryKey: ["applications"],
    queryFn: () => api.listApplications(),
  });

  return (
    <div className="workspace-page max-w-[1680px]">
      <PageIntro
        eyebrow="Pipeline control"
        title="Applications"
        description="A tactile command board for every role in motion. Drag cards between stages, switch to a dense table, and keep the next decision obvious."
        icon={BriefcaseBusiness}
        action={
          <button
            onClick={() => setOpen(true)}
            className="kinetic-button kinetic-button-primary"
          >
            <Plus className="size-3.5" /> Add job
          </button>
        }
      >
        <InfoChip tone="sage">{applications.length} roles tracked</InfoChip>
        <InfoChip>Drag to update status</InfoChip>
        <InfoChip tone="clay">{view === "kanban" ? "Spatial view" : "Dense view"}</InfoChip>
      </PageIntro>

      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.12, duration: 0.35 }}
        className="mt-5 flex justify-end"
      >
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
        </div>
      </motion.div>

      <div className="mt-4">
        {isLoading ? (
          <div className="loading-surface" />
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
