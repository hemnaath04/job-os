"use client";

import { useEffect, useState } from "react";

export type TocEntry = { id: string; label: string };

/**
 * Static per-page heading list (each page knows its own h2s), plus a
 * scroll-spy highlight so the active section is visible without JS-scanning
 * the DOM for headings on every render.
 */
export function Toc({ items }: { items: TocEntry[] }) {
  const [active, setActive] = useState(items[0]?.id);

  useEffect(() => {
    if (items.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActive(entry.target.id);
        }
      },
      { rootMargin: "-96px 0px -70% 0px" },
    );
    for (const item of items) {
      const el = document.getElementById(item.id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [items]);

  if (items.length === 0) return null;

  return (
    <nav aria-label="On this page" className="sticky top-24 hidden w-48 shrink-0 xl:block">
      <p className="mb-3 text-xs font-medium uppercase tracking-wider text-[color:var(--color-text-dim)]">
        On this page
      </p>
      <ul className="space-y-2 border-l border-[color:var(--color-border)] text-sm">
        {items.map((item) => (
          <li key={item.id}>
            <a
              href={`#${item.id}`}
              className={
                "-ml-px block border-l pl-3 transition-colors " +
                (active === item.id
                  ? "border-[color:var(--color-accent-ink)] text-[color:var(--color-text)]"
                  : "border-transparent text-[color:var(--color-text-muted)] hover:text-[color:var(--color-text)]")
              }
            >
              {item.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
