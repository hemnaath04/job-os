"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

/**
 * Last-resort boundary for a render error the app did not handle.
 *
 * It replaces the root layout when it fires, so there is no theme provider, no
 * sidebar and no tokens to rely on: the styles here are inline and literal on
 * purpose, because anything that depends on the app's CSS could be the very
 * thing that failed.
 *
 * Reporting is the point. Without this, a crash in a client component shows the
 * user a blank page and tells you nothing at all.
 */
export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string };
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "1.5rem",
          background: "#F1EFE3",
          color: "#2A2530",
          fontFamily:
            "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
        }}
      >
        <div style={{ maxWidth: "26rem", textAlign: "center" }}>
          <h1
            style={{
              fontSize: "1.25rem",
              fontWeight: 600,
              letterSpacing: "-0.02em",
              margin: 0,
            }}
          >
            Something went wrong
          </h1>
          <p
            style={{
              marginTop: "0.75rem",
              fontSize: "0.875rem",
              lineHeight: 1.6,
              color: "#635C68",
            }}
          >
            The error has been reported. Your applications and resumes are
            unaffected.
          </p>
          {/* The digest is what ties this page to the recorded event, so someone
              can quote it in a support message and have it be findable. */}
          {error.digest && (
            <p
              style={{
                marginTop: "0.75rem",
                fontFamily: "ui-monospace, monospace",
                fontSize: "0.6875rem",
                color: "#837B6E",
              }}
            >
              {error.digest}
            </p>
          )}
          <a
            href="/dashboard"
            style={{
              display: "inline-block",
              marginTop: "1.5rem",
              padding: "0.6rem 1.25rem",
              borderRadius: "0.7rem",
              background: "#FFE787",
              color: "#221F0E",
              fontSize: "0.875rem",
              fontWeight: 600,
              textDecoration: "none",
            }}
          >
            Back to dashboard
          </a>
        </div>
      </body>
    </html>
  );
}
