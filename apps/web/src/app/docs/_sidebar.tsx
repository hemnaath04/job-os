"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV } from "./_nav";
import { DocsSearch } from "./_search";

export function DocsSidebar() {
  const pathname = usePathname();

  return (
    <aside className="sticky top-24 hidden w-56 shrink-0 self-start lg:block">
      <div className="mb-4">
        <DocsSearch />
      </div>
      <nav aria-label="Docs" className="space-y-6 text-sm">
        {NAV.map((group) => (
          <div key={group.label}>
            <p className="mb-2 px-2 text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
              {group.label}
            </p>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const active = pathname === item.href;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href as never}
                      className={
                        "block rounded-lg px-2 py-1.5 transition " +
                        (active
                          ? "bg-[color:var(--color-surface-2)] font-medium text-[color:var(--color-text)]"
                          : "text-[color:var(--color-text-muted)] hover:bg-[color:var(--color-surface-2)] hover:text-[color:var(--color-text)]")
                      }
                    >
                      {item.title}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
    </aside>
  );
}
