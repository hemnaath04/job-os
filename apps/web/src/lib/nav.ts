/**
 * The shell's navigation lists, in one place so the two shells cannot drift.
 *
 * They live here rather than in sidebar.tsx so the invariants below can be
 * tested: the web suite runs `src/**\/*.test.ts` under node --test with no DOM
 * or component renderer, so a data module is the only thing a test can reach.
 * The two bugs this guards both shipped, and both were a list quietly losing
 * an entry rather than anything visibly breaking. See nav.test.ts.
 */
import type { Route } from "next";
import {
  BookOpenText,
  Briefcase,
  CalendarDays,
  FileSignature,
  FileText,
  LayoutDashboard,
  MessageSquareText,
  Radar,
  Settings as SettingsIcon,
  Sparkles,
  UserSquare2,
  type LucideIcon,
} from "lucide-react";

export type NavLinkItem = {
  href: Route;
  label: string;
  icon: LucideIcon;
};

export type NavItem = NavLinkItem & {
  section?: string;
};

export const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, section: "Overview" },
  { href: "/applications", label: "Applications", icon: Briefcase, section: "Pipeline" },
  { href: "/tailor", label: "AI Resume Tailor", icon: Sparkles, section: "Pipeline" },
  { href: "/interview", label: "Interview Prep", icon: MessageSquareText, section: "Pipeline" },
  { href: "/jobs", label: "Job Finder", icon: Radar, section: "Pipeline" },
  { href: "/resumes", label: "Resumes", icon: FileText, section: "Documents" },
  { href: "/cover-letters", label: "Cover Letters", icon: FileSignature, section: "Documents" },
  { href: "/profile", label: "Profile", icon: UserSquare2, section: "Documents" },
  { href: "/calendar", label: "Calendar", icon: CalendarDays, section: "Other" },
];

// The phone bar holds five targets at a comfortable tap size. It used to be
// `NAV.slice(0, 5)`, which silently dropped whatever came sixth: Resumes, Cover
// Letters, Profile and Calendar were all unreachable on a phone, with nothing
// on screen to suggest they existed. Four of nine pages, including the profile
// every tailored resume is built from.
//
// So four go in the bar and the rest go behind "More", which means adding a
// tenth page hides it in the sheet rather than deleting it from the product.
export const PRIMARY = NAV.slice(0, 4);
export const OVERFLOW = NAV.slice(4);

// The footer's own items, declared beside NAV so anything added here reaches
// both shells at once. They are kept out of NAV because NAV drives the primary
// nav and its two slices; folding these in would put Settings in the phone's
// bottom bar. Sign out is in neither list: it is an action, not a route.
export const FOOTER_NAV: NavLinkItem[] = [
  { href: "/docs" as Route, label: "Docs", icon: BookOpenText },
  { href: "/settings" as Route, label: "Settings", icon: SettingsIcon },
];

export const SECTIONS = ["Overview", "Pipeline", "Documents", "Other"] as const;
