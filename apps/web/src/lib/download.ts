"use client";

import { toast } from "sonner";
import { appwriteFileAuthHeaders } from "@/lib/appwrite/client";
import { appwriteConfig } from "@/lib/appwrite/config";

/** Whether this URL is a file on Appwrite rather than on our own origin. */
function isAppwriteFileUrl(url: string): boolean {
  return !!appwriteConfig.endpoint && url.startsWith(appwriteConfig.endpoint);
}

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
 *
 * Which credential goes with the request follows from the URL, not from a
 * feature flag, so the two can never drift apart. Our own origin takes the
 * cookies it already has. A file on Appwrite takes a JWT, because that origin's
 * session cookie is third-party and browsers drop it, which made an owned file
 * answer 404. See appwriteFileAuthHeaders.
 */
export async function downloadPdf(url: string, filename: string): Promise<void> {
  const toastId = toast.loading("Generating PDF…");
  try {
    if (!url) {
      throw new Error("This version does not have a finalized PDF yet.");
    }
    const res = await fetch(url, {
      cache: "no-store",
      ...(isAppwriteFileUrl(url)
        ? { headers: await appwriteFileAuthHeaders() }
        : { credentials: "include" as RequestCredentials }),
    });
    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText}`);
    }
    const contentType = res.headers.get("content-type") ?? "";
    if (!contentType.includes("pdf")) {
      // Backend was cold or the proxy timed out — Vercel handed us its
      // 404.html instead of our PDF. Don't save it.
      throw new Error("The stored file is not a valid PDF.");
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
