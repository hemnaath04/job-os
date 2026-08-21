"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import { AlertTriangle, ArrowUpRight, Loader2, RefreshCw } from "lucide-react";

/**
 * The one-page PDF, framed like a document instead of embedded raw against
 * the panel edge: a muted backdrop holds a shadowed white page, matching how
 * every resume-builder product in this category presents its preview.
 * `#toolbar=0&navpanes=0&scrollbar=0` asks a Chromium-based embedded viewer
 * to drop its own toolbar, since this pane already has one; Safari's own
 * embedded viewer ignores that fragment and shows its native chrome anyway,
 * which is a browser limit on what an iframe can ask for, not a bug here.
 *
 * Shared by the resume editor's Preview/Split modes and the tailor result
 * page, so "what will my resume actually look like" renders the same way
 * everywhere it's asked.
 */
export function PdfPreviewPane({ query }: { query: UseQueryResult<string, Error> }) {
  if (query.isLoading) {
    return (
      <div className="flex h-full items-center justify-center bg-[color:var(--color-surface-3)] p-6 sm:p-10">
        <div className="loading-surface aspect-[8.5/11] w-full max-w-[560px] rounded-sm" />
      </div>
    );
  }
  if (query.isError || !query.data) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-[color:var(--color-surface-3)] p-6 text-center">
        <AlertTriangle className="size-6 text-[color:var(--color-rose-ink)]" />
        <p className="max-w-xs text-sm text-[color:var(--color-text-muted)]">
          The draft preview could not be rendered.
        </p>
        <button
          onClick={() => query.refetch()}
          className="product-button product-button-secondary"
        >
          <RefreshCw className="size-4" /> Retry
        </button>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col bg-[color:var(--color-surface-3)]">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] px-3 py-2">
        <span className="flex items-center gap-1.5 text-xs font-medium text-[color:var(--color-text-dim)]">
          {query.isFetching ? (
            <>
              <Loader2 className="size-3 animate-spin" /> Updating…
            </>
          ) : (
            "Preview"
          )}
        </span>
        <a
          href={query.data}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-xs text-[color:var(--color-text-dim)] transition hover:text-[color:var(--color-text)]"
        >
          Open in new tab <ArrowUpRight className="size-3" />
        </a>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4 sm:p-8">
        <div className="mx-auto aspect-[8.5/11] w-full max-w-[560px] overflow-hidden rounded-sm bg-white shadow-[0_24px_50px_-24px_rgba(0,0,0,0.4)]">
          <iframe
            title="Resume draft preview"
            src={`${query.data}#toolbar=0&navpanes=0&scrollbar=0`}
            className="h-full w-full"
          />
        </div>
      </div>
    </div>
  );
}
