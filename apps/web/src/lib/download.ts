"use client";

import { toast } from "sonner";

/**
 * Fetch a backend URL, validate it's a real PDF, and trigger a browser download.
 *
 * We can't just use `<a download href>` because of the failure mode it
 * produces in our hosting layout:
 *
 *   1. Click <a download> → browser GETs `/api/backend/...`
 *   2. Vercel proxy forwards to Render (free tier — may be cold)
 *   3. Render wake takes 30-60s
 *   4. Vercel hobby plan kills the function ~60s and serves its own static
 *      404.html as the response body
 *   5. The browser dutifully writes that HTML to disk as `resume.pdf`
 *
 * Doing the download as `fetch + Blob` lets us:
 *   - Show a "Generating PDF…" state while we wait
 *   - Inspect the response content-type before saving
 *   - Show a real toast error ("backend is warming up, try again") instead
 *     of letting the user open a broken file
 */
export async function downloadPdf(url: string, filename: string): Promise<void> {
  const toastId = toast.loading("Generating PDF…");
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText}`);
    }
    const contentType = res.headers.get("content-type") ?? "";
    if (!contentType.includes("pdf")) {
      // Backend was cold or the proxy timed out — Vercel handed us its
      // 404.html instead of our PDF. Don't save it.
      throw new Error(
        "Backend served an unexpected response (probably warming up). Try again in 20-30s.",
      );
    }
    const blob = await res.blob();
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(objectUrl);
    toast.success("Downloaded", { id: toastId });
  } catch (e) {
    const msg = (e as Error).message;
    toast.error(`Couldn't download PDF: ${msg}`, { id: toastId });
  }
}
