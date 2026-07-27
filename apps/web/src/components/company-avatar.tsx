/** First-letter avatar with a deterministic gradient — premium feel without
 * needing a logo CDN. */

const PALETTE = [
  ["#6366F1", "#8B5CF6"],
  ["#06B6D4", "#3B82F6"],
  ["#A855F7", "#EC4899"],
  ["#10B981", "#5EEAD4"],
  ["#F59E0B", "#F5B544"],
  ["#F43F5E", "#FF6B8A"],
];

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

export function CompanyAvatar({
  name,
  size = 28,
  className = "",
}: {
  name: string;
  size?: number;
  className?: string;
}) {
  const letter = (name?.trim()?.[0] ?? "?").toUpperCase();
  const [from, to] = PALETTE[hash(name || "?") % PALETTE.length];
  return (
    <div
      className={`relative flex shrink-0 items-center justify-center overflow-hidden rounded-lg font-semibold text-[color:var(--color-text)] ${className}`}
      style={{
        width: size,
        height: size,
        fontSize: size * 0.45,
        background: `linear-gradient(135deg, ${from}, ${to})`,
        boxShadow: `0 0 18px -6px ${from}`,
      }}
      aria-hidden
    >
      <div className="absolute inset-0 bg-gradient-to-br from-[color:var(--color-cream)]/40 to-transparent" />
      <span className="relative">{letter}</span>
    </div>
  );
}
