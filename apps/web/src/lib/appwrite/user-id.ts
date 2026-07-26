import { createHash } from "node:crypto";

const VALID_APPWRITE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,35}$/;

/**
 * Clerk IDs normally already satisfy Appwrite's 36-character ID limit. The
 * deterministic fallback keeps the bridge safe if Clerk changes its ID shape.
 */
export function appwriteUserIdForClerk(clerkUserId: string): string {
  if (VALID_APPWRITE_ID.test(clerkUserId)) return clerkUserId;

  const digest = createHash("sha256").update(clerkUserId).digest("hex").slice(0, 30);
  return `clerk_${digest}`;
}
