"use client";

import Link from "next/link";
import type { Route } from "next";
import { usePathname } from "next/navigation";
import { SignOutButton, UserButton } from "@clerk/nextjs";
import type { LucideIcon } from "lucide-react";
import {
  CalendarDays,
  FileText,
  LayoutGrid,
  LogOut,
  Radar,
  Settings,
  Sparkles,
  UserSquare2,
} from "lucide-react";
import { QueryProvider } from "@/lib/query";
import { Toaster } from "sonner";

type NavItem = { href: Route; label: string; icon: LucideIcon };

const NAV: NavItem[] = [
  { href: "/applications", label: "Applications", icon: LayoutGrid },
  { href: "/jobs", label: "Discover", icon: Radar },
  { href: "/tailor", label: "Tailor", icon: Sparkles },
  { href: "/resumes", label: "Resumes", icon: FileText },
  { href: "/profile", label: "Profile", icon: UserSquare2 },
  { href: "/calendar", label: "Calendar", icon: CalendarDays },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <QueryProvider>
      <div className="flex min-h-screen">
        <aside className="sticky top-0 flex h-screen w-56 shrink-0 flex-col border-r border-white/5 bg-[color:var(--color-surface-1)]/40 px-3 py-5 backdrop-blur-xl">
          <Link href="/" className="mb-7 flex items-center gap-2 px-2">
            <div className="size-6 rounded-md bg-gradient-to-br from-[#7C5CFF] to-[#5EEAD4] shadow-[0_0_20px_-4px_#7C5CFF]" />
            <span className="font-mono text-sm tracking-tight">job.os</span>
          </Link>

          <nav className="flex flex-col gap-0.5 text-sm">
            {NAV.map(({ href, label, icon: Icon }) => {
              const active = pathname === href || pathname.startsWith(href + "/");
              return (
                <Link
                  key={href}
                  href={href}
                  className={
                    "flex items-center gap-2 rounded-md px-2 py-1.5 transition " +
                    (active
                      ? "bg-white/[0.06] text-white"
                      : "text-[color:var(--color-text-muted)] hover:bg-white/[0.04] hover:text-white")
                  }
                >
                  <Icon className="size-4" />
                  {label}
                </Link>
              );
            })}
          </nav>

          <div className="mt-auto flex flex-col gap-2 px-1">
            <Link
              href="/settings"
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-[color:var(--color-text-muted)] hover:bg-white/[0.04] hover:text-[color:var(--color-text)]"
            >
              <Settings className="size-4" /> Settings
            </Link>
            <SignOutButton>
              <button
                type="button"
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-[color:var(--color-text-muted)] hover:bg-white/[0.04] hover:text-[color:var(--color-rose)]"
              >
                <LogOut className="size-4" /> Sign out
              </button>
            </SignOutButton>
            <div className="mt-1 flex items-center gap-2 border-t border-white/[0.05] px-1 pt-3">
              <UserButton afterSignOutUrl="/" />
              <span className="text-xs text-[color:var(--color-text-dim)]">
                account
              </span>
            </div>
          </div>
        </aside>

        <main className="flex-1">{children}</main>
      </div>
      <Toaster theme="dark" position="bottom-right" />
    </QueryProvider>
  );
}
