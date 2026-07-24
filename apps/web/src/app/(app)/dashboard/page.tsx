import { auth } from "@clerk/nextjs/server";
import DashboardClient from "./dashboard-client";
import type { Application } from "@/lib/types";

const API = process.env.API_BASE_URL ?? "http://localhost:8000";

async function loadApplications(): Promise<Application[] | null> {
  const { userId, getToken } = await auth();
  if (!userId) return null;

  const token = await getToken();
  if (!token) return null;

  try {
    const response = await fetch(`${API}/api/v1/applications`, {
      headers: { authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok) return null;
    return (await response.json()) as Application[];
  } catch {
    // Preserve the existing client-side fallback when the API is unavailable.
    return null;
  }
}

export default async function DashboardPage() {
  const applications = await loadApplications();
  return <DashboardClient initialApplications={applications} />;
}
