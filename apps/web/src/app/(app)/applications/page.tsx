"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { BriefcaseBusiness, Inbox, LayoutGrid, Plus, Rows3 } from "lucide-react";
import { useState } from "react";
import { AddJobDialog } from "@/components/add-job-dialog";
import { ApplicationsTable } from "@/components/applications-table";
import { EmptyState } from "@/components/empty-state";
import { KanbanBoard } from "@/components/kanban-board";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { api } from "@/lib/api";
import type { Application, AppStatus } from "@/lib/types";

export default function ApplicationsPage() {
  const [view, setView] = useState<"kanban" | "table">("kanban");
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();

  const { data: applications = [], refetch, isLoading } = useQuery({
    queryKey: ["applications"],
    queryFn: () => api.listApplications(),
  });

  const updateApplication = useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: string;
      patch: Partial<Application>;
      optimisticBase?: Application;
    }) => api.patchApplication(id, patch),
    onMutate: async ({ id, patch, optimisticBase }) => {
      await queryClient.cancelQueries({ queryKey: ["applications"] });
      const previous =
        queryClient.getQueryData<Application[]>(["applications"]) ?? [];
      queryClient.setQueryData<Application[]>(["applications"], (current = []) => {
        const updated = current.map((application) =>
          application.id === id
            ? {
                ...application,
                ...patch,
                updated_at: new Date().toISOString(),
              }
            : application,
        );
        if (!updated.some((application) => application.id === id) && optimisticBase) {
          updated.unshift({
            ...optimisticBase,
            ...patch,
            updated_at: new Date().toISOString(),
          });
        }
        return updated;
      });
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["applications"], context.previous);
      }
    },
    onSuccess: (saved) => {
      queryClient.setQueryData<Application[]>(["applications"], (current = []) => {
        const updated = current.map((application) =>
          application.id === saved.id ? saved : application,
        );
        return updated.some((application) => application.id === saved.id)
          ? updated
          : [saved, ...updated];
      });
    },
  });

  const archiveApplication = useMutation({
    mutationFn: (id: string) => api.archiveApplication(id),
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: ["applications"] });
      const previous =
        queryClient.getQueryData<Application[]>(["applications"]) ?? [];
      queryClient.setQueryData<Application[]>(["applications"], (current = []) =>
        current.filter((application) => application.id !== id),
      );
      return { previous };
    },
    onError: (_error, _id, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["applications"], context.previous);
      }
    },
  });

  const moveApplication = (id: string, status: AppStatus) =>
    updateApplication.mutateAsync({ id, patch: { status } });

  const restoreApplication = (application: Application) =>
    updateApplication.mutateAsync({
      id: application.id,
      patch: { archived: false },
      optimisticBase: application,
    });

  return (
    <div className="workspace-page max-w-[1680px]">
      <PageIntro
        eyebrow="Application pipeline"
        title="Applications"
        description="Move roles between stages, switch to a table, and keep every follow-up visible."
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
        <InfoChip>Instant status updates</InfoChip>
        <InfoChip tone="clay">{view === "kanban" ? "Kanban view" : "Table view"}</InfoChip>
      </PageIntro>

      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.12, duration: 0.35 }}
        className="mt-5 flex justify-end"
      >
        <div className="flex items-center gap-2">
          <div className="flex rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-0.5">
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
          <KanbanBoard
            applications={applications}
            onMove={moveApplication}
            onArchive={(id) => archiveApplication.mutateAsync(id)}
            onRestore={restoreApplication}
          />
        ) : (
          <ApplicationsTable
            applications={applications}
            onArchive={(id) => archiveApplication.mutateAsync(id)}
            onRestore={restoreApplication}
          />
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
          ? "font-bold text-[color:var(--color-on-accent)]"
          : "font-medium text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]")
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
