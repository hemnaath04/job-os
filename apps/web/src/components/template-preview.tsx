"use client";

import { FileText, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { appwriteWorkspace } from "@/lib/appwrite/workspace";
import type { ResumeTemplate } from "@/lib/types";

/**
 * The stored sample render of a template, as an image the browser can show.
 *
 * Appwrite wants a JWT for a file read, so the file is fetched and turned into
 * an object URL rather than linked. The URL is revoked on unmount, or a page
 * that re-renders a grid of six of these leaks a blob every time.
 */
export function useStoredFileUrl(fileId: string | null | undefined): {
  url: string | null;
  loading: boolean;
  failed: boolean;
} {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!fileId) {
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
        const objectUrl = await appwriteWorkspace.storedFileObjectUrl(fileId);
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
  }, [fileId]);

  return { url, loading, failed };
}

/**
 * A template's preview: the first page of the real render.
 *
 * Deliberately not a mock or an illustration. What is shown here is the PDF the
 * renderer produced from clearly invented sample data, so the preview cannot
 * promise a look the renderer does not actually produce. Builtins carry a PNG of
 * page one; a template built from an upload has only the PDF, since rasterising
 * needs a tool the browser does not have.
 */
export function TemplatePreview({
  template,
  className = "",
}: {
  template: ResumeTemplate;
  className?: string;
}) {
  const png = useStoredFileUrl(template.preview_file_id);
  const pdf = useStoredFileUrl(png.url || png.loading ? null : template.preview_pdf_file_id);

  if (png.url) {
    return (
      // A rendered page, so the alt text says what it is rather than describing
      // a design nobody can read from a thumbnail.
      <img
        src={png.url}
        alt={`Sample resume rendered with the ${template.name} template`}
        className={`h-full w-full object-cover object-top ${className}`}
      />
    );
  }
  if (pdf.url) {
    return (
      <object
        data={`${pdf.url}#toolbar=0&navpanes=0&view=FitH`}
        type="application/pdf"
        aria-label={`Sample resume rendered with the ${template.name} template`}
        className={`pointer-events-none h-full w-full ${className}`}
      />
    );
  }
  return (
    <div
      className={`flex h-full w-full items-center justify-center bg-[color:var(--color-surface-2)] ${className}`}
    >
      {png.loading || pdf.loading ? (
        <Loader2 className="size-4 animate-spin text-[color:var(--color-text-dim)]" />
      ) : (
        <FileText className="size-4 text-[color:var(--color-text-dim)]" />
      )}
    </div>
  );
}
