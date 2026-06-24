import type { AppStatus } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

// Per-status colors. Text is intentionally always white — readability beats
// chromatic differentiation here, and the leading dot + ring carry the
// per-status color identity anyway (Linear / Asana convention).
const STATUS_STYLE: Record<AppStatus, { from: string; to: string; ring: string }> = {
  wishlist:            { from: "#A1A1AE", to: "#52525B", ring: "#A1A1AE" },
  ready_to_apply:      { from: "#06B6D4", to: "#3B82F6", ring: "#38BDF8" },
  applied:             { from: "#CCFF00", to: "#FFFF00", ring: "#CCFF00" },
  oa_received:         { from: "#F59E0B", to: "#F5B544", ring: "#F5B544" },
  interview_scheduled: { from: "#10B981", to: "#34D399", ring: "#34D399" },
  offer:               { from: "#06B6D4", to: "#5EEAD4", ring: "#5EEAD4" },
  accepted:            { from: "#10B981", to: "#5EEAD4", ring: "#34D399" },
  rejected:            { from: "#F43F5E", to: "#FF6B8A", ring: "#FF6B8A" },
  withdrawn:           { from: "#71717A", to: "#52525B", ring: "#71717A" },
  ghosted:             { from: "#52525B", to: "#27272A", ring: "#71717A" },
};

export function StatusPill({ status, size = "sm" }: { status: AppStatus; size?: "xs" | "sm" }) {
  const s = STATUS_STYLE[status];
  const cls =
    size === "xs"
      ? "text-[9px] px-1.5 py-0.5"
      : "text-[10px] px-2 py-0.5";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full font-semibold uppercase tracking-wider text-white ${cls}`}
      style={{
        background: `linear-gradient(135deg, ${s.from}33, ${s.to}33)`,
        boxShadow: `inset 0 0 0 1px ${s.ring}80`,
      }}
    >
      <span
        className="size-1.5 rounded-full"
        style={{
          background: `linear-gradient(135deg, ${s.from}, ${s.to})`,
          boxShadow: `0 0 8px -1px ${s.ring}`,
        }}
      />
      {STATUS_LABELS[status]}
    </span>
  );
}
