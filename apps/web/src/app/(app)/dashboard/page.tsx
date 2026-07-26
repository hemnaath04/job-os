import DashboardClient from "./dashboard-client";

export default function DashboardPage() {
  // The authenticated browser reads Appwrite directly. Waiting for Render
  // during server rendering made every dashboard refresh pay its cold start.
  return <DashboardClient initialApplications={null} />;
}
