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

export default nextConfig;
