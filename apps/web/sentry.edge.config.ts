// Edge runtime Sentry. This app does not opt any route into the edge runtime,
// but Clerk's middleware runs here, so it is the one place edge errors can
// appear and the file is cheap to keep correct.
import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env.SENTRY_DSN ?? process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: process.env.VERCEL_ENV ?? "development",
  enableLogs: true,
  tracesSampleRate: 0.1,
  sendDefaultPii: false,
});
