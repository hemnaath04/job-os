import { auth, currentUser } from "@clerk/nextjs/server";
import { AppwriteException, Client, Users } from "node-appwrite";
import { NextResponse } from "next/server";
import { appwriteUserIdForClerk } from "@/lib/appwrite/user-id";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function serverConfig() {
  const endpoint = process.env.NEXT_PUBLIC_APPWRITE_ENDPOINT;
  const projectId = process.env.NEXT_PUBLIC_APPWRITE_PROJECT_ID;
  const apiKey = process.env.APPWRITE_API_KEY;
  if (!endpoint || !projectId || !apiKey) {
    throw new Error("Appwrite server environment is incomplete");
  }
  return { endpoint, projectId, apiKey };
}

/**
 * The Appwrite id the signed-in Clerk user is entitled to, and nothing else.
 *
 * The browser cannot work this out for itself: the mapping lives in
 * `appwriteUserIdForClerk`, which hashes with `node:crypto` and so is
 * server-only. Without it the client has no way to tell whose Appwrite session
 * it is holding, which is exactly how a signed-out user's session gets reused
 * by the next person to sign in on the same browser. Deliberately cheap: it
 * touches Clerk and a hash, never Appwrite, so calling it on every session
 * check costs a round trip and nothing more.
 */
export async function GET() {
  const { userId: clerkUserId } = await auth();
  if (!clerkUserId) {
    return NextResponse.json({ detail: "not authenticated" }, { status: 401 });
  }
  return NextResponse.json(
    { userId: appwriteUserIdForClerk(clerkUserId) },
    { headers: { "Cache-Control": "no-store" } },
  );
}

export async function POST() {
  const { userId: clerkUserId } = await auth();
  if (!clerkUserId) {
    return NextResponse.json({ detail: "not authenticated" }, { status: 401 });
  }

  try {
    const { endpoint, projectId, apiKey } = serverConfig();
    const clerkUser = await currentUser();
    const appwriteUserId = appwriteUserIdForClerk(clerkUserId);
    const client = new Client()
      .setEndpoint(endpoint)
      .setProject(projectId)
      .setKey(apiKey);
    const users = new Users(client);

    try {
      await users.get({ userId: appwriteUserId });
    } catch (error) {
      if (!(error instanceof AppwriteException) || error.code !== 404) throw error;
      const email = clerkUser?.primaryEmailAddress?.emailAddress;
      const name =
        [clerkUser?.firstName, clerkUser?.lastName].filter(Boolean).join(" ") ||
        clerkUser?.username ||
        undefined;
      await users.create({
        userId: appwriteUserId,
        email,
        name,
      });
    }

    const token = await users.createToken({
      userId: appwriteUserId,
      length: 64,
      expire: 60,
    });

    return NextResponse.json(
      { userId: appwriteUserId, secret: token.secret },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    console.error("[appwrite-session]", error);
    return NextResponse.json(
      { detail: "could not establish Appwrite session" },
      { status: 500 },
    );
  }
}
