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
import { Building2, Calendar, MapPin } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
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
        {KANBAN_STATUSES.map((status) => {
          const items = applications.filter((a) => a.status === status);
          return <Column key={status} status={status} items={items} />;
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
      className={`flex w-72 shrink-0 flex-col rounded-[var(--radius-card)] border p-3 transition ${
        isOver
          ? "border-[#7C5CFF]/50 bg-[#7C5CFF]/[0.04]"
          : "border-white/5 bg-white/[0.015]"
      }`}
    >
      <div className="mb-3 flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <StatusDot status={status} />
          <span className="text-sm font-medium">{STATUS_LABELS[status]}</span>
        </div>
        <span className="font-mono text-xs text-[color:var(--color-text-dim)]">
          {items.length}
        </span>
      </div>
      <div className="flex flex-col gap-2">
        {items.map((a) => (
          <DraggableCard key={a.id} app={a} />
        ))}
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
  return (
    <div
      className={`glass cursor-grab rounded-[0.75rem] p-3 ${
        dragging ? "rotate-2 shadow-2xl" : "hover:bg-white/[0.04]"
      }`}
    >
      <div className="font-medium text-sm">{app.job.title}</div>
      <div className="mt-1 flex items-center gap-1 text-xs text-[color:var(--color-text-muted)]">
        <Building2 className="size-3" /> {company}
      </div>
      {app.job.location && (
        <div className="mt-0.5 flex items-center gap-1 text-xs text-[color:var(--color-text-dim)]">
          <MapPin className="size-3" /> {app.job.location}
        </div>
      )}
      {app.next_action_at && (
        <div className="mt-2 flex items-center gap-1 text-xs text-[color:var(--color-amber)]">
          <Calendar className="size-3" /> {app.next_action_label || "next action"}
        </div>
      )}
    </div>
  );
}

function StatusDot({ status }: { status: AppStatus }) {
  const colors: Record<AppStatus, string> = {
    wishlist: "bg-white/30",
    ready_to_apply: "bg-sky-400",
    applied: "bg-[#7C5CFF]",
    oa_received: "bg-amber-400",
    interview_scheduled: "bg-emerald-400",
    offer: "bg-[#5EEAD4]",
    accepted: "bg-[#5EEAD4]",
    rejected: "bg-rose-400",
    withdrawn: "bg-zinc-500",
    ghosted: "bg-zinc-500",
  };
  return <span className={`size-1.5 rounded-full ${colors[status]}`} />;
}
