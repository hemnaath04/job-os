import { withSentryConfig } from "@sentry/nextjs";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // typedRoutes off: Clerk catchall routes /sign-in/[[...sign-in]] and
  // /sign-up/[[...sign-up]] don't satisfy the static checker cleanly and
  // it errored on every Link to "/sign-in" during the Vercel build.
  // Bring back when Clerk ships per-route exports.
  // No rewrites — /api/backend/* is handled by an auth-injecting Route Handler
  // at app/api/backend/[...path]/route.ts (forwards Clerk JWT as Bearer).
};

// Sentry wraps the config to upload source maps at build time. Without this the
// production stack traces are minified and effectively unreadable, which is most
// of the value gone. Needs SENTRY_AUTH_TOKEN in the build environment; the build
// carries on without it rather than failing, so a missing token degrades to
// "reports arrive, but ugly" instead of a broken deploy.
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  // Quiet unless something is wrong, since this runs on every Vercel build.
  silent: !process.env.CI,
  // Source maps are uploaded to Sentry and then deleted from the deployment, so
  // the readable source of a private app is not served publicly.
  sourcemaps: { deleteSourcemapsAfterUpload: true },
  // Routes browser telemetry through the app's own origin, so an ad blocker
  // cutting off requests to Sentry's domain does not silently stop reporting.
  tunnelRoute: "/monitoring",
  // Strips Sentry's own debug logging from the client bundle. Replaces the
  // deprecated `disableLogger`, which the build warned about.
  webpack: { treeshake: { removeDebugLogging: true } },
});
