// Next.js instrumentation hook. Loads the right Sentry config per runtime and
// forwards server-side request errors.
//
// `onRequestError` is what catches throws inside server components, middleware
// and route handlers. Without it those failures are logged by the platform and
// never reach Sentry, which is most of the server surface in an App Router app.
// Requires @sentry/nextjs 8.28 or newer on Next 15.
import * as Sentry from "@sentry/nextjs";

export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    await import("../sentry.server.config");
  }
  if (process.env.NEXT_RUNTIME === "edge") {
    await import("../sentry.edge.config");
  }
}

export const onRequestError = Sentry.captureRequestError;
