// Server-side Sentry for the Node runtime: route handlers, server components,
// and the auth-injecting backend proxy.
//
// This is the runtime worth watching most closely on the web side. The proxy at
// app/api/backend/[...path]/route.ts carries the Clerk JWT, and /api/discover
// fans out to a dozen job boards, so a failure here is invisible to the browser
// beyond a failed request.
import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env.SENTRY_DSN ?? process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: process.env.VERCEL_ENV ?? "development",
  enableLogs: true,
  tracesSampleRate: 0.1,

  // Off deliberately. With it on, Sentry attaches request bodies and headers,
  // and the bodies moving through this server are resumes and the headers carry
  // a session token.
  sendDefaultPii: false,
});
