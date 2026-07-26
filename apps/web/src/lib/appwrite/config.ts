export const appwriteConfig = {
  endpoint: process.env.NEXT_PUBLIC_APPWRITE_ENDPOINT ?? "",
  projectId: process.env.NEXT_PUBLIC_APPWRITE_PROJECT_ID ?? "",
  databaseId: process.env.NEXT_PUBLIC_APPWRITE_DATABASE_ID ?? "job-os",
  applicationsTableId:
    process.env.NEXT_PUBLIC_APPWRITE_APPLICATIONS_TABLE_ID ?? "application_cards",
};

export type PipelineBackend = "legacy" | "appwrite";

export const pipelineBackend: PipelineBackend =
  process.env.NEXT_PUBLIC_PIPELINE_BACKEND === "appwrite" ? "appwrite" : "legacy";

export const isAppwritePipelineEnabled = pipelineBackend === "appwrite";

export function requirePublicAppwriteConfig() {
  const missing = Object.entries(appwriteConfig)
    .filter(([, value]) => !value)
    .map(([key]) => key);

  if (missing.length > 0) {
    throw new Error(`Appwrite is enabled but missing: ${missing.join(", ")}`);
  }

  return appwriteConfig;
}
