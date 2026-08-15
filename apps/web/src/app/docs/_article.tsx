import type { ReactNode } from "react";
import { PageNav } from "./_page-nav";
import { surroundingPages } from "./_nav";
import { Toc, type TocEntry } from "./_toc";

export function Article({
  href,
  title,
  description,
  toc = [],
  children,
}: {
  href: string;
  title: string;
  description: string;
  toc?: TocEntry[];
  children: ReactNode;
}) {
  const { prev, next } = surroundingPages(href);

  return (
    <div className="flex min-w-0 flex-1 gap-10">
      <article className="min-w-0 flex-1">
        <h1 className="text-3xl font-medium tracking-[-0.02em] text-[color:var(--color-text)]">
          {title}
        </h1>
        <p className="mt-2 text-base text-[color:var(--color-text-muted)]">{description}</p>
        <div className="mt-8">{children}</div>
        <PageNav prev={prev} next={next} />
      </article>
      <Toc items={toc} />
    </div>
  );
}
