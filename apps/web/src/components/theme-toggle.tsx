/**
 * Deprecated location. The real toggle lives in ./shell/theme-toggle and has
 * done since before this file existed: it also syncs across open tabs and
 * animates the icon swap, neither of which a second copy would have inherited.
 *
 * Kept only as a re-export so no import breaks. Safe to delete once nothing
 * points here.
 */
export { ThemeToggle } from "@/components/shell/theme-toggle";
