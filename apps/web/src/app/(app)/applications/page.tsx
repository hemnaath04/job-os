"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BriefcaseBusiness, Inbox, Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { AddJobDialog } from "@/components/add-job-dialog";
import {
  ApplicationInspector,
} from "@/components/applications/application-inspector";
import { ApplicationList } from "@/components/applications/application-list";
import {
  ApplicationToolbar,
  type ApplicationSort,
  type ApplicationsView,
} from "@/components/applications/application-toolbar";
import { StageTabs, type StageFilter } from "@/components/applications/stage-tabs";
import { ApplicationsTable } from "@/components/applications-table";
import { EmptyState } from "@/components/empty-state";
import { KanbanBoard } from "@/components/kanban-board";
import { InfoChip, PageIntro } from "@/components/page-intro";
import { api } from "@/lib/api";
import { buildProfileVocab } from "@/lib/discover/fit-score";
import { scoreApplicationJob } from "@/lib/discover/job-fit";
import { isActiveStatus, matchesStatuses, PRIMARY_STAGES, SECONDARY_STAGES } from "@/lib/application-stage";
import type { Application, AppStatus } from "@/lib/types";

const ALL_STAGES = [...PRIMARY_STAGES, ...SECONDARY_STAGES];

export default function ApplicationsPage() {
  const [view, setView] = useState<ApplicationsView>("list");
  const [stage, setStage] = useState<StageFilter>("all");
  const [query, setQuery] = useState("");
  const [location, setLocation] = useState("");
  const [workType, setWorkType] = useState("");
  const [minMatch, setMinMatch] = useState("");
  const [sort, setSort] = useState<ApplicationSort>("updated");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const queryClient = useQueryClient();

  const { data: applications = [], refetch, isLoading } = useQuery({
    queryKey: ["applications"],
    queryFn: () => api.listApplications(),
  });
  const { data: archivedApplications = [] } = useQuery({
    queryKey: ["applications", "archived"],
    queryFn: () => api.listApplications({ archived: true }),
    enabled: stage === "archived",
  });
  // The verified profile powers the AI match score, the same way it does on
  // the Job Finder page. Loaded once; scores are computed client-side.
  const { data: facts = [] } = useQuery({
    queryKey: ["facts"],
    queryFn: () => api.listFacts(),
  });
  const vocab = useMemo(() => buildProfileVocab(facts), [facts]);

  const matchScores = useMemo(() => {
    const scores = new Map<string, number>();
    for (const application of applications) {
      const fit = scoreApplicationJob(application.job, vocab);
      if (fit.confident) scores.set(application.id, fit.score);
    }
    return scores;
  }, [applications, vocab]);

  const updateApplication = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<Application>; optimisticBase?: Application }) =>
      api.patchApplication(id, patch),
    onMutate: async ({ id, patch, optimisticBase }) => {
      await queryClient.cancelQueries({ queryKey: ["applications"] });
      const previous = queryClient.getQueryData<Application[]>(["applications"]) ?? [];
      queryClient.setQueryData<Application[]>(["applications"], (current = []) => {
        const updated = current.map((application) =>
          application.id === id
            ? { ...application, ...patch, updated_at: new Date().toISOString() }
            : application,
        );
        if (!updated.some((application) => application.id === id) && optimisticBase) {
          updated.unshift({ ...optimisticBase, ...patch, updated_at: new Date().toISOString() });
        }
        return updated;
      });
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(["applications"], context.previous);
    },
    onSuccess: (saved) => {
      queryClient.setQueryData<Application[]>(["applications"], (current = []) => {
        const updated = current.map((application) => (application.id === saved.id ? saved : application));
        return updated.some((application) => application.id === saved.id) ? updated : [saved, ...updated];
      });
    },
  });

  const archiveApplication = useMutation({
    mutationFn: (id: string) => api.archiveApplication(id),
    onMutate: async (id) => {
      await queryClient.cancelQueries({ queryKey: ["applications"] });
      const previous = queryClient.getQueryData<Application[]>(["applications"]) ?? [];
      queryClient.setQueryData<Application[]>(["applications"], (current = []) =>
        current.filter((application) => application.id !== id),
      );
      return { previous };
    },
    onError: (_error, _id, context) => {
      if (context?.previous) queryClient.setQueryData(["applications"], context.previous);
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

  const stageDef = ALL_STAGES.find((candidate) => candidate.key === stage) ?? PRIMARY_STAGES[0];
  const baseApplications = stage === "archived" ? archivedApplications : applications;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const threshold = minMatch ? Number(minMatch) : null;
    const rows = baseApplications.filter((application) => {
      if (stage !== "archived" && !matchesStatuses(application.status, stageDef.statuses)) return false;
      if (location && application.job.location !== location) return false;
      if (workType && application.job.remote !== workType) return false;
      if (threshold !== null && (matchScores.get(application.id) ?? -1) < threshold) return false;
      if (!q) return true;
      const haystack = [
        application.job.title,
        application.job.company?.name,
        application.job.location,
        application.notes,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });

    const sorted = [...rows];
    if (sort === "company") {
      sorted.sort((a, b) => (a.job.company?.name ?? "").localeCompare(b.job.company?.name ?? ""));
    } else if (sort === "applied") {
      sorted.sort((a, b) => (b.applied_at ?? "").localeCompare(a.applied_at ?? ""));
    } else if (sort === "match") {
      sorted.sort((a, b) => (matchScores.get(b.id) ?? -1) - (matchScores.get(a.id) ?? -1));
    } else {
      sorted.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    }
    return sorted;
  }, [baseApplications, stage, stageDef, location, workType, minMatch, query, sort, matchScores]);

  // The selection survives a filter or search change, per the brief, so the
  // inspector is looked up from the full set rather than the filtered one.
  const selected = useMemo(
    () => applications.find((application) => application.id === selectedId) ?? null,
    [applications, selectedId],
  );

  useEffect(() => {
    if (selectedId && applications.some((application) => application.id === selectedId)) return;
    setSelectedId(filtered[0]?.id ?? null);
    // Only when the current selection has gone missing entirely (initial
    // load, or the selected row was archived) -- not on every filter change,
    // which is exactly the case this must not react to.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applications, selectedId]);

  const activeCount = applications.filter((application) => isActiveStatus(application.status)).length;
  const interviewCount = applications.filter((application) => application.status === "interview_scheduled").length;
  const offerCount = applications.filter(
    (application) => application.status === "offer" || application.status === "accepted",
  ).length;

  return (
    <div className="workspace-page flex h-full max-w-[1680px] flex-col">
      <PageIntro
        eyebrow="Application pipeline"
        title="Applications"
        description={`${applications.length} applications · ${activeCount} active · ${interviewCount} interviews · ${offerCount} offers`}
        icon={BriefcaseBusiness}
        action={
          <button onClick={() => setAddOpen(true)} className="kinetic-button kinetic-button-primary">
            <Plus className="size-3.5" /> Add job
          </button>
        }
      >
        <InfoChip tone="sage">{applications.length} roles tracked</InfoChip>
        <InfoChip>Instant status updates</InfoChip>
      </PageIntro>

      {isLoading ? (
        <div className="loading-surface mt-5" />
      ) : applications.length === 0 ? (
        <EmptyState
          icon={Inbox}
          title="No applications yet"
          description="Add a job from a URL and it'll show up here. Track status, set follow-ups, and tailor resumes per role."
          cta={{ href: "/jobs", label: "Find internships" }}
        />
      ) : (
        <div className="mt-5 flex min-h-0 flex-1 flex-col gap-4">
          <ApplicationToolbar
            applications={baseApplications}
            query={query}
            onQueryChange={setQuery}
            location={location}
            onLocationChange={setLocation}
            workType={workType}
            onWorkTypeChange={setWorkType}
            minMatch={minMatch}
            onMinMatchChange={setMinMatch}
            sort={sort}
            onSortChange={setSort}
            view={view}
            onViewChange={setView}
          />
          <StageTabs
            applications={applications}
            archivedCount={archivedApplications.length}
            active={stage}
            onChange={setStage}
          />

          {view === "board" ? (
            <KanbanBoard
              applications={filtered}
              onMove={moveApplication}
              onArchive={(id) => archiveApplication.mutateAsync(id)}
              onRestore={restoreApplication}
            />
          ) : view === "table" ? (
            <ApplicationsTable
              applications={filtered}
              onArchive={(id) => archiveApplication.mutateAsync(id)}
              onRestore={restoreApplication}
            />
          ) : (
            // flex-1 fills whatever vertical room the page has, which is right
            // when there are enough rows to use it -- but "fill" has no upper
            // bound of its own, so a short filtered list (or any list at all on
            // a tall external display) stretches this bordered panel into a
            // mostly-empty card instead of leaving that space as plain page
            // background. max-h caps the panel; flex-1 still fills up to it.
            <div className="workspace-panel grid min-h-0 flex-1 max-h-[820px] grid-cols-1 overflow-hidden lg:grid-cols-[minmax(320px,38%)_1fr]">
              <div className={`min-h-0 ${selected ? "hidden lg:block" : ""}`}>
                <ApplicationList
                  applications={filtered}
                  selectedId={selectedId}
                  matchScores={matchScores}
                  onSelect={(application) => setSelectedId(application.id)}
                />
              </div>
              <div
                className={`min-h-0 border-l border-[color:var(--color-border)] lg:flex ${
                  selected ? "flex" : "hidden"
                }`}
              >
                {selected ? (
                  <ApplicationInspector
                    application={selected}
                    vocab={vocab}
                    onPatch={(id, patch) => updateApplication.mutateAsync({ id, patch })}
                    onArchive={(id) => archiveApplication.mutateAsync(id)}
                    onRestore={restoreApplication}
                    onClose={() => setSelectedId(null)}
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center p-6 text-center text-sm text-[color:var(--color-text-dim)]">
                    Select an application to see its details.
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      <AddJobDialog open={addOpen} onOpenChange={setAddOpen} onCreated={() => refetch()} />
    </div>
  );
}
