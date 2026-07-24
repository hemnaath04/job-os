import type { AppStatus } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

// Per-status colors. Text is intentionally always white — readability beats
// chromatic differentiation here, and the leading dot + ring carry the
// per-status color identity anyway (Linear / Asana convention).
const STATUS_STYLE: Record<AppStatus, { from: string; to: string; ring: string }> = {
  wishlist:            { from: "#A1A1AE", to: "#52525B", ring: "#A1A1AE" },
  ready_to_apply:      { from: "#7F9CCB", to: "#91A8D8", ring: "#7F9CCB" },
  applied:             { from: "#9AA7FF", to: "#C7CDF0", ring: "#9AA7FF" },
  oa_received:         { from: "#C49252", to: "#D0A15E", ring: "#D0A15E" },
  interview_scheduled: { from: "#718F7D", to: "#8BAE98", ring: "#7FA28E" },
  offer:               { from: "#789789", to: "#A0B8A8", ring: "#91AA9A" },
  accepted:            { from: "#829A7B", to: "#B0C2A8", ring: "#A7B99F" },
  rejected:            { from: "#B96972", to: "#D58C93", ring: "#CC7A82" },
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
