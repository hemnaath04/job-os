"use client";

import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { AnimatePresence, motion } from "framer-motion";
import { Calendar, ExternalLink, MapPin, Sparkles } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { CompanyAvatar } from "@/components/company-avatar";
import { StatusPill } from "@/components/status-pill";
import { api } from "@/lib/api";
import type { Application, AppStatus } from "@/lib/types";
import { KANBAN_STATUSES, STATUS_LABELS } from "@/lib/types";

export function KanbanBoard({
  applications,
  onChange,
}: {
  applications: Application[];
  onChange: () => void;
}) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const [dragged, setDragged] = useState<Application | null>(null);

  async function onDragEnd(e: DragEndEvent) {
    setDragged(null);
    const overId = e.over?.id;
    const appId = e.active.id as string;
    if (!overId) return;
    const target = overId as AppStatus;
    const app = applications.find((a) => a.id === appId);
    if (!app || app.status === target) return;

    try {
      await api.patchApplication(appId, { status: target });
      toast.success(`Moved to ${STATUS_LABELS[target]}`);
      onChange();
    } catch (err) {
      toast.error(`Couldn't update: ${(err as Error).message}`);
    }
  }

  return (
    <DndContext
      sensors={sensors}
      onDragStart={(e) =>
        setDragged(applications.find((a) => a.id === e.active.id) ?? null)
      }
      onDragEnd={onDragEnd}
    >
      <div className="flex gap-3 overflow-x-auto pb-4">
        {KANBAN_STATUSES.map((status, idx) => {
          const items = applications.filter((a) => a.status === status);
          return (
            <motion.div
              key={status}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: idx * 0.03, duration: 0.25 }}
            >
              <Column status={status} items={items} />
            </motion.div>
          );
        })}
      </div>
      <DragOverlay>{dragged ? <Card app={dragged} dragging /> : null}</DragOverlay>
    </DndContext>
  );
}

function Column({ status, items }: { status: AppStatus; items: Application[] }) {
  const { setNodeRef, isOver } = useDroppable({ id: status });
  return (
    <div
      ref={setNodeRef}
      className={
        "flex w-72 shrink-0 flex-col rounded-[var(--radius-card-lg)] border p-3 transition " +
        (isOver
          ? "border-[#A855F7]/50 bg-[#A855F7]/[0.05] shadow-[0_0_40px_-12px_#A855F7]"
          : "border-[color:var(--color-border)] bg-white/[0.015]")
      }
    >
      <div className="mb-3 flex items-center justify-between px-1">
        <StatusPill status={status} />
        <span className="font-mono text-xs text-[color:var(--color-text-dim)]">
          {items.length}
        </span>
      </div>
      <div className="flex flex-col gap-2">
        <AnimatePresence>
          {items.map((a) => (
            <motion.div
              key={a.id}
              layout
              initial={{ opacity: 0, scale: 0.97 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              transition={{ duration: 0.18 }}
            >
              <DraggableCard app={a} />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}

function DraggableCard({ app }: { app: Application }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: app.id });
  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      className={isDragging ? "opacity-30" : ""}
    >
      <Card app={app} />
    </div>
  );
}

function Card({ app, dragging = false }: { app: Application; dragging?: boolean }) {
  const company = app.job.company?.name ?? "Unknown";
  const sourceUrl = app.job.source_url ?? null;
  const stopDrag = (e: React.SyntheticEvent) => e.stopPropagation();
  function openJD() {
    if (sourceUrl) window.open(sourceUrl, "_blank", "noopener,noreferrer");
  }
  return (
    <div
      onDoubleClick={openJD}
      title={sourceUrl ? "Double-click to open the original JD" : undefined}
      className={
        "group glass cursor-grab rounded-[0.875rem] p-3 transition-all " +
        (dragging
          ? "rotate-2 shadow-2xl"
          : "hover:bg-white/[0.04] hover:shadow-[0_0_30px_-12px_#A855F7]")
      }
    >
      <div className="flex items-start gap-2.5">
        <CompanyAvatar name={company} size={28} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1 truncate text-sm font-medium">
            <span className="truncate">{app.job.title}</span>
            {sourceUrl && (
              <a
                href={sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                onPointerDown={stopDrag}
                onClick={stopDrag}
                onDoubleClick={stopDrag}
                title="Open original JD"
                aria-label="Open original job description"
                className="shrink-0 text-[color:var(--color-text-dim)] transition hover:text-[color:var(--color-violet)]"
              >
                <ExternalLink className="size-3" />
              </a>
            )}
          </div>
          <div className="truncate text-xs text-[color:var(--color-text-muted)]">
            {company}
          </div>
        </div>
      </div>
      {app.job.location && (
        <div className="mt-2 flex items-center gap-1 text-xs text-[color:var(--color-text-dim)]">
          <MapPin className="size-3" /> {app.job.location}
        </div>
      )}
      {app.next_action_at && (
        <div className="mt-1.5 flex items-center gap-1 text-xs text-[color:var(--color-amber)]">
          <Calendar className="size-3" /> {app.next_action_label || "next action"}
        </div>
      )}
      {!dragging && (
        <div className="mt-2 flex justify-end opacity-0 transition-opacity group-hover:opacity-100">
          <Link
            href={{ pathname: "/tailor", query: { job_id: app.job.id } }}
            onPointerDown={stopDrag}
            onClick={stopDrag}
            onDoubleClick={stopDrag}
            className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-2 py-0.5 text-[10px] font-medium text-black shadow-[0_0_20px_-8px_var(--color-purple)]"
          >
            <Sparkles className="size-2.5" /> Tailor
          </Link>
        </div>
      )}
    </div>
  );
}
