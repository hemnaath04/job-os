"use client";

import { UserButton, useClerk } from "@clerk/nextjs";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  Bookmark,
  ChevronsLeft,
  ChevronsRight,
  MoreHorizontal,
  LogOut,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { BrandMark } from "@/components/brand-mark";
import { FOOTER_NAV, NAV, OVERFLOW, PRIMARY, SECTIONS, type NavLinkItem } from "@/lib/nav";
import { clearAppwriteSession } from "@/lib/appwrite/client";

export function Sidebar() {
  const pathname = usePathname();
  const reduceMotion = useReducedMotion();
  const [collapsed, setCollapsed] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const moreActive = OVERFLOW.some(
    (item) => pathname === item.href || pathname.startsWith(item.href + "/"),
  );
  // A phone's back gesture changes the route without unmounting this, so the
  // sheet has to close itself or it hangs over the page you just navigated to.
  useEffect(() => {
    setMoreOpen(false);
  }, [pathname]);
  const width = collapsed ? 72 : 232;
  const { signOut } = useClerk();

  // Clerk and Appwrite hold separate sessions, and ending the Clerk one does
  // not end the Appwrite one. Drop Appwrite first so a signed-out user's
  // resumes and profile are not left readable in the browser for whoever signs
  // in next. Appwrite first, and never blocking: if it fails, signing out of
  // Clerk still has to happen, and the identity check in ensureAppwriteSession
  // catches the leftover session on the next sign-in.
  async function signOutEverywhere() {
    await clearAppwriteSession();
    await signOut({ redirectUrl: "/" });
  }

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
          <BrandMark className="drop-shadow-[0_12px_16px_rgba(233,198,74,.28)]" />
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
        <nav aria-label="Primary" className="flex flex-1 flex-col gap-3 overflow-y-auto px-3 pb-3 text-sm">
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
          {FOOTER_NAV.map((item) => (
            <NavLink
              key={item.href}
              item={item}
              active={pathname === item.href || pathname.startsWith(item.href + "/")}
              collapsed={collapsed}
              reduceMotion={Boolean(reduceMotion)}
            />
          ))}
          <button
            type="button"
            onClick={signOutEverywhere}
            className={
              "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[color:var(--color-text-muted)] transition hover:bg-[color:var(--color-surface-2)] hover:text-[color:var(--color-rose-ink)] active:scale-[.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-kiwi)] " +
              (collapsed ? "justify-center" : "")
            }
            title="Sign out"
            aria-label="Sign out"
          >
            <LogOut className="size-4 shrink-0" aria-hidden="true" />
            {!collapsed && <span className="truncate">Sign out</span>}
          </button>
          <div
            className={
              "mt-2 flex items-center gap-2 border-t border-[color:var(--color-border)] pt-3 " +
              (collapsed ? "flex-col justify-center" : "px-1")
            }
          >
            <UserButton />
            {!collapsed && (
              <span className="truncate text-xs text-[color:var(--color-text-dim)]">
                account
              </span>
            )}
          </div>
          <button
            onClick={() => setCollapsed((c) => !c)}
            className="mt-2 inline-flex items-center justify-center rounded-lg border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] p-1.5 text-[color:var(--color-text-dim)] transition hover:bg-[color:var(--color-surface-hover)] hover:text-[color:var(--color-text)] active:scale-[.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-kiwi)]"
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
      {/* Clear of the home indicator on a notched phone, where a flat 0.75rem
          put the bar underneath it. */}
      {/* A token surface, not black. On the warm light theme a black bar was both
          off-language and unreadable: the active icon uses --color-text, which
          measured 1.13:1 against it. */}
      <nav className="fixed inset-x-3 bottom-[max(0.75rem,env(safe-area-inset-bottom))] z-50 flex h-14 items-center justify-around rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)]/85 px-1.5 shadow-[var(--shadow-glass-hover)] backdrop-blur-2xl lg:hidden" aria-label="Primary navigation">
        {PRIMARY.map((item) => {
          const Icon = item.icon;
          const active = pathname === item.href || pathname.startsWith(item.href + "/");
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`relative flex size-10 items-center justify-center rounded-xl transition active:scale-[.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-kiwi)] ${active ? "text-[color:var(--color-text)]" : "text-[color:var(--color-text-dim)] hover:text-[color:var(--color-text)]"}`}
              aria-label={item.label}
              aria-current={active ? "page" : undefined}
            >
              {active && <motion.span layoutId="mobile-nav-active" className="absolute inset-0 rounded-xl border border-[color:var(--color-kiwi)]/20 bg-[color:var(--color-kiwi)]/12" transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 380, damping: 30 }} />}
              <Icon className="relative size-[18px]" />
            </Link>
          );
        })}
        <button
          type="button"
          onClick={() => setMoreOpen((open) => !open)}
          aria-expanded={moreOpen}
          aria-controls="mobile-nav-more"
          aria-label={moreOpen ? "Close more pages and account" : "More pages and account"}
          className={`relative flex size-10 items-center justify-center rounded-xl transition active:scale-[.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-kiwi)] ${
            moreActive || moreOpen
              ? "text-[color:var(--color-text)]"
              : "text-[color:var(--color-text-dim)] hover:text-[color:var(--color-text)]"
          }`}
        >
          {moreActive && !moreOpen && (
            <motion.span
              layoutId="mobile-nav-active"
              className="absolute inset-0 rounded-xl border border-[color:var(--color-kiwi)]/20 bg-[color:var(--color-kiwi)]/12"
              transition={reduceMotion ? { duration: 0 } : { type: "spring", stiffness: 380, damping: 30 }}
            />
          )}
          <MoreHorizontal className="relative size-[18px]" />
        </button>
      </nav>
      {moreOpen && (
        <>
          {/* Tapping anywhere else closes it, which is what a sheet on a phone
              is expected to do. */}
          <button
            type="button"
            aria-label="Close more pages"
            onClick={() => setMoreOpen(false)}
            className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[2px] lg:hidden"
          />
          <div
            id="mobile-nav-more"
            className="fixed inset-x-3 bottom-[calc(max(0.75rem,env(safe-area-inset-bottom))+3.75rem)] z-50 overflow-hidden rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)]/95 shadow-[var(--shadow-glass-hover)] backdrop-blur-2xl lg:hidden"
          >
            {OVERFLOW.map((item) => {
              const Icon = item.icon;
              const active = pathname === item.href || pathname.startsWith(item.href + "/");
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setMoreOpen(false)}
                  aria-current={active ? "page" : undefined}
                  className={`flex items-center gap-3 border-b border-[color:var(--color-border)] px-4 py-3 text-sm last:border-b-0 ${
                    active
                      ? "bg-[color:var(--color-kiwi)]/12 text-[color:var(--color-text)]"
                      : "text-[color:var(--color-text-muted)]"
                  }`}
                >
                  <Icon className="size-4 shrink-0" />
                  {item.label}
                </Link>
              );
            })}
            {/* The account block. Until this existed there was no way to reach
                Settings, Docs or Sign out on a phone at all: they live in the
                sidebar footer, and the sidebar is `lg:flex`, so below 1024px
                the only signed-in exit was clearing site data. */}
            <div className="flex flex-col border-t-2 border-[color:var(--color-border)]">
              {FOOTER_NAV.map((item) => {
                const Icon = item.icon;
                const active =
                  pathname === item.href || pathname.startsWith(item.href + "/");
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={() => setMoreOpen(false)}
                    aria-current={active ? "page" : undefined}
                    className={`flex items-center gap-3 border-b border-[color:var(--color-border)] px-4 py-3 text-sm ${
                      active
                        ? "bg-[color:var(--color-kiwi)]/12 text-[color:var(--color-text)]"
                        : "text-[color:var(--color-text-muted)]"
                    }`}
                  >
                    <Icon className="size-4 shrink-0" />
                    {item.label}
                  </Link>
                );
              })}
              {/* Not wired to setMoreOpen: signOutEverywhere navigates to "/"
                  and this whole tree unmounts, so closing the sheet first would
                  only race the redirect. */}
              <button
                type="button"
                onClick={signOutEverywhere}
                className="flex items-center gap-3 border-b border-[color:var(--color-border)] px-4 py-3 text-left text-sm text-[color:var(--color-text-muted)] transition active:scale-[.98] active:bg-[color:var(--color-surface-2)]"
              >
                <LogOut className="size-4 shrink-0" aria-hidden="true" />
                Sign out
              </button>
              {/* Clerk portals its popover to the body, so the sheet's
                  `overflow-hidden` does not clip it. */}
              <div className="flex items-center gap-3 px-4 py-3">
                <UserButton />
                <span className="text-xs text-[color:var(--color-text-dim)]">
                  account
                </span>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
}

function NavLink({
  item,
  active,
  collapsed,
  reduceMotion,
}: {
  item: NavLinkItem;
  active: boolean;
  collapsed: boolean;
  reduceMotion: boolean;
}) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      className={
        "relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 transition active:scale-[.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-kiwi)] " +
        (active
          ? "font-semibold text-[color:var(--color-text)]"
          : "text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-2)] hover:text-[color:var(--color-text)]") +
        (collapsed ? " justify-center" : "")
      }
      title={collapsed ? item.label : undefined}
      // Collapsed, the label is gone from the DOM, so name the link explicitly
      // rather than leaning on `title` as a last-resort accessible name.
      aria-label={collapsed ? item.label : undefined}
      aria-current={active ? "page" : undefined}
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
