"use client";

import { SignOutButton, UserButton } from "@clerk/nextjs";
import { AnimatePresence, motion } from "framer-motion";
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
  const [collapsed, setCollapsed] = useState(false);
  const width = collapsed ? 72 : 232;

  return (
    <motion.aside
      animate={{ width }}
      transition={{ type: "spring", stiffness: 220, damping: 28 }}
      className="sticky top-0 z-30 flex h-screen shrink-0 flex-col border-r border-[color:var(--color-border)] bg-[color:var(--color-surface-1)]/40 backdrop-blur-xl"
    >
      {/* Brand */}
      <Link
        href="/dashboard"
        className="flex items-center gap-2.5 px-4 pt-5 pb-6"
      >
        <BrandMark className="drop-shadow-[0_0_14px_rgba(204,255,0,0.28)]" />
        <AnimatePresence initial={false}>
          {!collapsed && (
            <motion.span
              key="brand"
              initial={{ opacity: 0, x: -4 }}
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
                    initial={{ opacity: 0 }}
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
        />
        <SignOutButton>
          <button
            type="button"
            className={
              "flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-[color:var(--color-text-muted)] transition hover:bg-white/[0.04] hover:text-[color:var(--color-rose)] " +
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
          className="mt-2 inline-flex items-center justify-center rounded-lg border border-[color:var(--color-border)] bg-white/[0.02] p-1.5 text-[color:var(--color-text-dim)] transition hover:bg-white/[0.05] hover:text-white"
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
  );
}

function NavLink({
  item,
  active,
  collapsed,
}: {
  item: { href: Route; label: string; icon: LucideIcon };
  active: boolean;
  collapsed: boolean;
}) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      className={
        "relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 transition " +
        (active
          ? "font-semibold text-black"
          : "text-[color:var(--color-text-muted)] hover:bg-white/[0.04] hover:text-white") +
        (collapsed ? " justify-center" : "")
      }
      title={collapsed ? item.label : undefined}
    >
      {/* Active background — full neon chip so the dark text reads clearly */}
      {active && (
        <motion.span
          layoutId="nav-active"
          className="absolute inset-0 rounded-lg bg-gradient-brand"
          style={{ boxShadow: "0 0 28px -6px rgba(204,255,0,0.55)" }}
          transition={{ type: "spring", stiffness: 380, damping: 30 }}
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
