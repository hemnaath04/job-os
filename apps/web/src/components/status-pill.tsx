import type { AppStatus } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

const STATUS_STYLE: Record<AppStatus, { from: string; to: string; text: string; ring: string }> = {
  wishlist: { from: "#A1A1AE", to: "#52525B", text: "#E4E4E7", ring: "#A1A1AE" },
  ready_to_apply: { from: "#06B6D4", to: "#3B82F6", text: "#E0F2FE", ring: "#38BDF8" },
  applied: { from: "#CCFF00", to: "#FFFF00", text: "#DFFF00", ring: "#CCFF00" },
  oa_received: { from: "#F59E0B", to: "#F5B544", text: "#FFF7ED", ring: "#F5B544" },
  interview_scheduled: { from: "#10B981", to: "#34D399", text: "#ECFDF5", ring: "#34D399" },
  offer: { from: "#06B6D4", to: "#5EEAD4", text: "#ECFEFF", ring: "#5EEAD4" },
  accepted: { from: "#10B981", to: "#5EEAD4", text: "#ECFEFF", ring: "#34D399" },
  rejected: { from: "#F43F5E", to: "#FF6B8A", text: "#FFE4E6", ring: "#FF6B8A" },
  withdrawn: { from: "#71717A", to: "#52525B", text: "#D4D4D8", ring: "#71717A" },
  ghosted: { from: "#52525B", to: "#27272A", text: "#A1A1AE", ring: "#71717A" },
};

export function StatusPill({ status, size = "sm" }: { status: AppStatus; size?: "xs" | "sm" }) {
  const s = STATUS_STYLE[status];
  const cls =
    size === "xs"
      ? "text-[9px] px-1.5 py-0.5"
      : "text-[10px] px-2 py-0.5";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full font-semibold uppercase tracking-wider ${cls}`}
      style={{
        background: `linear-gradient(135deg, ${s.from}20, ${s.to}25)`,
        color: s.text,
        boxShadow: `inset 0 0 0 1px ${s.ring}40`,
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
