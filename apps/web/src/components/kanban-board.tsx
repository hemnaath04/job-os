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
import { ArrowDownRight, Calendar, ExternalLink, MapPin, Sparkles, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { CompanyAvatar } from "@/components/company-avatar";
import { StatusPill } from "@/components/status-pill";
import type { Application, AppStatus } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

const BOARD_STATUSES: AppStatus[] = ["applied", "interview_scheduled", "rejected", "offer"];

function belongsToVisibleStage(status: AppStatus, visibleStage: "wishlist" | AppStatus) {
  if (visibleStage === "wishlist") return status === "wishlist" || status === "ready_to_apply";
  if (visibleStage === "applied") return status === "applied" || status === "oa_received";
  if (visibleStage === "offer") return status === "offer" || status === "accepted";
  if (visibleStage === "rejected") return status === "rejected" || status === "withdrawn" || status === "ghosted";
  return status === visibleStage;
}

export function KanbanBoard({
  applications,
  onMove,
  onArchive,
  onRestore,
}: {
  applications: Application[];
  onMove: (id: string, status: AppStatus) => Promise<unknown>;
  onArchive: (id: string) => Promise<unknown>;
  onRestore: (application: Application) => Promise<unknown>;
}) {
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const [dragged, setDragged] = useState<Application | null>(null);
  const wishlist = applications.filter((app) => belongsToVisibleStage(app.status, "wishlist"));

  async function onDragEnd(e: DragEndEvent) {
    setDragged(null);
    const overId = e.over?.id;
    const appId = e.active.id as string;
    if (!overId) return;
    const target = overId as AppStatus;
    const app = applications.find((a) => a.id === appId);
    if (!app || app.status === target) return;

    try {
      await onMove(appId, target);
      toast.success(`Moved to ${STATUS_LABELS[target]}`);
    } catch (err) {
      toast.error(`Couldn't update: ${(err as Error).message}`);
    }
  }

  async function onDelete(app: Application) {
    try {
      await onArchive(app.id);
      toast.success(`Archived "${app.job.title}"`, {
        description: app.job.company?.name ?? undefined,
        action: {
          label: "Undo",
          onClick: async () => {
            try {
              await onRestore(app);
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
    <DndContext
      sensors={sensors}
      onDragStart={(e) =>
        setDragged(applications.find((a) => a.id === e.active.id) ?? null)
      }
      onDragEnd={onDragEnd}
    >
      <motion.section
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="workspace-panel mb-5 p-4 sm:p-5"
      >
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold tracking-tight">Wishlist</h2>
              <span className="info-chip min-h-0 px-2 py-0.5">{wishlist.length}</span>
            </div>
            <p className="mt-1 text-xs text-[color:var(--color-text-dim)]">
              Roles you are considering. Drag a card into Applied when you submit.
            </p>
          </div>
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-[#b8c0ef]">
            Drag to Applied <ArrowDownRight className="size-3.5" />
          </div>
        </div>
        {wishlist.length > 0 ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <AnimatePresence>
              {wishlist.map((app) => (
                <motion.div
                  key={app.id}
                  layout
                  initial={{ opacity: 0, scale: 0.98 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.96 }}
                >
                  <DraggableCard app={app} onDelete={onDelete} compact />
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-white/[0.08] px-4 py-5 text-center text-xs text-[color:var(--color-text-dim)]">
            Your wishlist is clear. Add a role whenever something catches your eye.
          </div>
        )}
      </motion.section>

      <div className="grid gap-4 pb-5 lg:grid-cols-2 2xl:grid-cols-4">
        {BOARD_STATUSES.map((status, idx) => {
          const items = applications.filter((a) => belongsToVisibleStage(a.status, status));
          return (
            <motion.div
              key={status}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: idx * 0.03, duration: 0.25 }}
            >
              <Column status={status} items={items} onDelete={onDelete} />
            </motion.div>
          );
        })}
      </div>
      <DragOverlay>{dragged ? <Card app={dragged} dragging /> : null}</DragOverlay>
    </DndContext>
  );
}

function Column({
  status,
  items,
  onDelete,
}: {
  status: AppStatus;
  items: Application[];
  onDelete: (app: Application) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: status });
  return (
    <div
      ref={setNodeRef}
      className={
        "workspace-panel flex min-h-[18rem] min-w-0 flex-col p-3.5 transition " +
        (isOver
          ? "border-[#9AA7FF]/40 bg-[#9AA7FF]/[0.045] shadow-[0_18px_50px_-32px_rgba(107,120,210,.7)]"
          : "border-[color:var(--color-border)]")
      }
    >
      <div className="mb-3 flex items-center justify-between px-1">
        <StatusPill status={status} />
        <span className="font-mono text-xs text-white/60">{items.length}</span>
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
              <DraggableCard app={a} onDelete={onDelete} />
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}

function DraggableCard({
  app,
  onDelete,
  compact = false,
}: {
  app: Application;
  onDelete: (app: Application) => void;
  compact?: boolean;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: app.id });
  return (
    <div
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      className={isDragging ? "opacity-30" : ""}
    >
      <Card app={app} onDelete={onDelete} compact={compact} />
    </div>
  );
}

function Card({
  app,
  dragging = false,
  onDelete,
  compact = false,
}: {
  app: Application;
  dragging?: boolean;
  onDelete?: (app: Application) => void;
  compact?: boolean;
}) {
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
        "group cursor-grab rounded-[0.95rem] border border-white/[0.075] bg-[#111419]/90 transition-all " +
        (compact ? "p-4 " : "p-3.5 ") +
        (dragging
          ? "rotate-2 shadow-2xl"
          : "hover:-translate-y-0.5 hover:border-[#9AA7FF]/20 hover:bg-[#181c22]")
      }
    >
      <div className="flex items-start gap-2.5">
        <CompanyAvatar name={company} size={28} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1 truncate text-sm font-medium text-white">
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
                className="shrink-0 text-white/40 transition hover:text-[color:var(--color-violet)]"
              >
                <ExternalLink className="size-3" />
              </a>
            )}
          </div>
          <div className="truncate text-xs text-white/70">{company}</div>
        </div>
      </div>
      {app.job.location && (
        <div className="mt-2 flex items-center gap-1 text-xs text-white/50">
          <MapPin className="size-3" /> {app.job.location}
        </div>
      )}
      {app.next_action_at && (
        <div className="mt-1.5 flex items-center gap-1 text-xs text-[color:var(--color-amber)]">
          <Calendar className="size-3" /> {app.next_action_label || "next action"}
        </div>
      )}
      {!dragging && (
        <div className={`mt-2 flex items-center justify-end gap-2 transition-opacity ${compact ? "opacity-100" : "opacity-0 group-hover:opacity-100"}`}>
          {onDelete && (
            <button
              onPointerDown={stopDrag}
              onClick={(e) => {
                stopDrag(e);
                onDelete(app);
              }}
              onDoubleClick={stopDrag}
              title="Archive (move out of pipeline)"
              aria-label="Archive application"
              className="inline-flex items-center justify-center rounded-full border border-white/10 bg-white/[0.03] p-1 text-white/60 transition hover:bg-rose-400/15 hover:text-rose-300"
            >
              <Trash2 className="size-3" />
            </button>
          )}
          <Link
            href={{ pathname: "/tailor", query: { job_id: app.job.id } }}
            onPointerDown={stopDrag}
            onClick={stopDrag}
            onDoubleClick={stopDrag}
            className="inline-flex items-center gap-1 rounded-full bg-gradient-brand px-2 py-0.5 text-[10px] font-semibold text-black shadow-[0_10px_24px_-18px_rgba(107,120,210,.45)]"
          >
            <Sparkles className="size-2.5" /> Tailor
          </Link>
        </div>
      )}
    </div>
  );
}
