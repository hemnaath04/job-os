"use client";

import { SignOutButton, UserButton } from "@clerk/nextjs";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Bookmark,
  CalendarDays,
  ChevronsLeft,
  ChevronsRight,
  FileText,
  LayoutDashboard,
  LayoutGrid,
  LogOut,
  Radar,
  Settings as SettingsIcon,
  Sparkles,
  UserSquare2,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import type { Route } from "next";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { BrandMark } from "@/components/brand-mark";

type NavItem = {
  href: Route;
  label: string;
  icon: LucideIcon;
  section?: string;
};

const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, section: "Overview" },
  { href: "/applications", label: "Applications", icon: LayoutGrid, section: "Pipeline" },
  { href: "/tailor", label: "AI Resume Tailor", icon: Sparkles, section: "Pipeline" },
  { href: "/jobs", label: "Internship Finder", icon: Radar, section: "Pipeline" },
  { href: "/resumes", label: "Resumes", icon: FileText, section: "Documents" },
  { href: "/profile", label: "Profile", icon: UserSquare2, section: "Documents" },
  { href: "/calendar", label: "Calendar", icon: CalendarDays, section: "Other" },
];

const SECTIONS = ["Overview", "Pipeline", "Documents", "Other"] as const;

export function Sidebar() {
  const pathname = usePathname();
  const reduceMotion = useReducedMotion();
  const [collapsed, setCollapsed] = useState(false);
  const width = collapsed ? 72 : 232;

  return (
    <>
    <motion.aside
      animate={{ width }}
      transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 220, damping: 28 }}
      className="sticky top-0 z-30 hidden h-[100dvh] shrink-0 flex-col border-r border-[color:var(--color-border)] bg-[color:var(--color-surface-1)]/75 backdrop-blur-xl lg:flex"
    >
      {/* Brand */}
      <Link
        href="/dashboard"
        className="flex items-center gap-2.5 px-4 pt-5 pb-6"
      >
        <BrandMark className="drop-shadow-[0_12px_16px_rgba(107,120,210,.28)]" />
        <AnimatePresence initial={false}>
          {!collapsed && (
            <motion.span
              key="brand"
              initial={reduceMotion ? false : { opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -4 }}
              className="font-mono text-sm tracking-tight whitespace-nowrap"
            >
              job.os
            </motion.span>
          )}
        </AnimatePresence>
      </Link>

      {/* Nav */}
      <nav className="flex flex-1 flex-col gap-3 overflow-y-auto px-3 pb-3 text-sm">
        {SECTIONS.map((section) => {
          const items = NAV.filter((n) => n.section === section);
          if (items.length === 0) return null;
          return (
            <div key={section} className="flex flex-col gap-0.5">
              <AnimatePresence initial={false}>
                {!collapsed && (
                  <motion.div
                    key={`${section}-label`}
                    initial={reduceMotion ? false : { opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="px-2 pb-1.5 text-[10px] font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]"
                  >
                    {section}
                  </motion.div>
                )}
              </AnimatePresence>
              {items.map((item) => (
                <NavLink
                  key={item.href}
                  item={item}
                  active={
                    pathname === item.href || pathname.startsWith(item.href + "/")
                  }
                  collapsed={collapsed}
                  reduceMotion={Boolean(reduceMotion)}
                />
              ))}
            </div>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="flex flex-col gap-1 border-t border-[color:var(--color-border)] px-3 py-3 text-sm">
        <NavLink
          item={{ href: "/settings" as Route, label: "Settings", icon: SettingsIcon }}
          active={pathname === "/settings"}
          collapsed={collapsed}
          reduceMotion={Boolean(reduceMotion)}
        />
        <SignOutButton>
          <button
            type="button"
            className={
              "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-2)] hover:text-[color:var(--color-rose)] active:scale-[.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-kiwi)] " +
              (collapsed ? "justify-center" : "")
            }
            title="Sign out"
          >
            <LogOut className="size-4 shrink-0" />
            {!collapsed && <span className="truncate">Sign out</span>}
          </button>
        </SignOutButton>
        <div
          className={
            "mt-2 flex items-center gap-2 border-t border-[color:var(--color-border)] pt-3 " +
            (collapsed ? "justify-center" : "px-1")
          }
        >
          <UserButton afterSignOutUrl="/" />
          {!collapsed && (
            <span className="truncate text-xs text-[color:var(--color-text-dim)]">
              account
            </span>
          )}
        </div>
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="mt-2 inline-flex items-center justify-center rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-1.5 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)] active:scale-[.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-kiwi)]"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? (
            <ChevronsRight className="size-3.5" />
          ) : (
            <ChevronsLeft className="size-3.5" />
          )}
        </button>
      </div>
    </motion.aside>
    <nav className="fixed inset-x-3 bottom-3 z-50 flex h-14 items-center justify-around rounded-2xl border border-[color:var(--color-border)] bg-black/80 px-1.5 shadow-[0_18px_60px_rgba(0,0,0,.65)] backdrop-blur-2xl lg:hidden" aria-label="Primary navigation">
      {NAV.slice(0, 5).map((item) => {
        const Icon = item.icon;
        const active = pathname === item.href || pathname.startsWith(item.href + "/");
        return (
          <Link
            key={item.href}
            href={item.href}
            className={`relative flex size-10 items-center justify-center rounded-xl transition active:scale-[.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-kiwi)] ${active ? "text-[color:var(--color-text)]" : "text-[color:var(--color-text-dim)] hover:text-[color:var(--color-text)]"}`}
            aria-label={item.label}
          >
            {active && <motion.span layoutId="mobile-nav-active" className="absolute inset-0 rounded-xl border border-[color:var(--color-kiwi)]/20 bg-[color:var(--color-kiwi)]/12" transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 380, damping: 30 }} />}
            <Icon className="relative size-[18px]" />
          </Link>
        );
      })}
    </nav>
    </>
  );
}

function NavLink({
  item,
  active,
  collapsed,
  reduceMotion,
}: {
  item: { href: Route; label: string; icon: LucideIcon };
  active: boolean;
  collapsed: boolean;
  reduceMotion: boolean;
}) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      className={
        "relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 transition active:scale-[.985] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-kiwi)] " +
        (active
          ? "font-semibold text-[color:var(--color-text)]"
          : "text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-2)] hover:text-[color:var(--color-text)]") +
        (collapsed ? " justify-center" : "")
      }
      title={collapsed ? item.label : undefined}
    >
      {active && (
        <motion.span
          layoutId="nav-active"
          className="absolute inset-0 rounded-lg border border-[color:var(--color-kiwi)]/15 bg-[color:var(--color-kiwi)]/[0.09]"
          transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 380, damping: 30 }}
        />
      )}
      {active && !collapsed && (
        <motion.span
          layoutId="nav-indicator"
          className="absolute left-0 h-4 w-0.5 rounded-full bg-[color:var(--color-kiwi)]"
          transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 380, damping: 30 }}
        />
      )}
      <Icon className="relative size-4 shrink-0" />
      {!collapsed && (
        <span className="relative truncate text-sm">{item.label}</span>
      )}
    </Link>
  );
}

export const SIDEBAR_NAV = NAV;
export const SAVED_ICON = Bookmark;
