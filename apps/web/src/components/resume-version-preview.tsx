"use client";

import { FileText, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { pdfObjectUrl } from "@/lib/download";

/** A resume version's own rendered PDF as an object URL — same idea as
 * `useStoredFileUrl` in template-preview.tsx, but for a resume's final PDF
 * rather than a template's stored sample render. */
function useVersionPdfPreview(downloadUrl: string | null): {
  url: string | null;
  loading: boolean;
  failed: boolean;
} {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!downloadUrl) {
      setUrl(null);
      setFailed(false);
      return;
    }
    let cancelled = false;
    let created: string | null = null;
    setLoading(true);
    setFailed(false);
    void (async () => {
      try {
        const objectUrl = await pdfObjectUrl(downloadUrl);
        if (cancelled) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        created = objectUrl;
        setUrl(objectUrl);
      } catch {
        if (!cancelled) setFailed(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [downloadUrl]);

  return { url, loading, failed };
}

/**
 * A resume's own content as its thumbnail, rather than a generic icon.
 *
 * Unlike a template, a resume has no separately-rendered PNG — only whatever
 * PDF the version itself produced — so this embeds that PDF directly and lets
 * the browser rasterise page one, the same trick `TemplatePreview` uses for a
 * custom template that only has a PDF.
 */
export function ResumeVersionPreview({
  downloadUrl,
  label,
  className = "",
}: {
  downloadUrl: string | null;
  label: string;
  className?: string;
}) {
  const { url, loading, failed } = useVersionPdfPreview(downloadUrl);

  if (url) {
    return (
      <object
        data={`${url}#toolbar=0&navpanes=0&view=FitH`}
        type="application/pdf"
        aria-label={label}
        className={`pointer-events-none h-full w-full ${className}`}
      />
    );
  }
  return (
    <div
      className={`flex h-full w-full items-center justify-center bg-[color:var(--color-surface-2)] ${className}`}
    >
      {loading ? (
        <Loader2 className="size-4 animate-spin text-[color:var(--color-text-dim)]" />
      ) : (
        <FileText
          className="size-5 text-[color:var(--color-text-dim)]"
          aria-label={failed ? "Preview failed to load" : "No preview available"}
        />
      )}
    </div>
  );
}
