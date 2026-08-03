// Browser-side Sentry. Next.js loads this file automatically on the client.
//
// The DSN comes from an env var rather than the literal the Sentry wizard would
// have written here, because this repo is public. A DSN is not a secret (it ends
// up in the bundle either way), but a value baked into a committed file is one
// more thing to rotate by hand.
import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: process.env.NEXT_PUBLIC_VERCEL_ENV ?? "development",

  // Every log line, so a user reporting a problem can be answered from what was
  // already recorded rather than from a guess.
  enableLogs: true,

  // Sampled rather than off. Enough to see a slow page without paying for a
  // trace on every navigation.
  tracesSampleRate: 0.1,

  // Session replay: only ever on a session that actually broke. Replays are the
  // most invasive thing Sentry can collect, and this app holds resumes, so
  // recording healthy sessions would be gathering people's CVs for no reason.
  replaysSessionSampleRate: 0,
  replaysOnErrorSampleRate: 0.1,

  // Text and inputs are masked in what replay we do collect. Resume fields,
  // email addresses and job notes are all user content.
  integrations: [
    Sentry.replayIntegration({ maskAllText: true, blockAllMedia: true }),
  ],

  sendDefaultPii: false,
});

// Required for navigation instrumentation in the App Router.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
