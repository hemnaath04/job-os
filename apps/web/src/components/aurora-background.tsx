/**
 * Decorative drifting colour wash, warm-palette version of the aurora effect.
 *
 * Purely presentational and inert: `aria-hidden`, no pointer events, and it
 * paints behind its siblings. The animation and the colour stops live in
 * globals.css under `.aurora` so they read from the theme tokens and flip with
 * the theme, and so reduced motion can hold them still in one place.
 *
 * Intended for the public surfaces only, the auth brand panel and the landing
 * page. Do not put it behind the workspace pages.
 */
export function AuroraBackground({ className }: { className?: string }) {
  return <div aria-hidden="true" className={`aurora ${className ?? ""}`} />;
}
