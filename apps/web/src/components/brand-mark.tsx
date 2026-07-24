import { cn } from "@/lib/utils";

export function BrandMark({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 64 64"
      className={cn("size-7 shrink-0", className)}
    >
      <rect width="64" height="64" rx="16" fill="#080A08" />
      <rect
        x="1"
        y="1"
        width="62"
        height="62"
        rx="15"
        fill="none"
        stroke="#CCFF00"
        strokeOpacity="0.24"
        strokeWidth="2"
      />
      <path
        d="M18 15h29v9H36v15c0 9-5.5 14-15 14h-5v-9h5c4 0 6-2 6-6V24h-9V15Z"
        fill="#CCFF00"
      />
      <circle cx="47" cy="47" r="5" fill="#FFFF00" />
    </svg>
  );
}
