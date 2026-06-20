/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  typedRoutes: true, // moved out of experimental in Next 15.4+
  // No rewrites — /api/backend/* is handled by an auth-injecting Route Handler
  // at app/api/backend/[...path]/route.ts (forwards Clerk JWT as Bearer).
};

export default nextConfig;
