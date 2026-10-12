import type { ReactNode } from "react";

/**
 * A label for work that is still running: "Tailoring…", "Searching…".
 *
 * Used in place of a spinner beside the text, so a waiting button has one
 * moving thing instead of two. Keep the spinner where an icon swaps in for
 * another icon and the label stays the same. The styling lives in
 * `.text-shimmer` in globals.css, which falls back to plain text under
 * reduced motion.
 */
export function TextShimmer({
  className = "",
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return <span className={`text-shimmer ${className}`}>{children}</span>;
}
