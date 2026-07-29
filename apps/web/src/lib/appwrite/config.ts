export const appwriteConfig = {
  endpoint: process.env.NEXT_PUBLIC_APPWRITE_ENDPOINT ?? "",
  projectId: process.env.NEXT_PUBLIC_APPWRITE_PROJECT_ID ?? "",
  databaseId: process.env.NEXT_PUBLIC_APPWRITE_DATABASE_ID ?? "job-os",
  applicationsTableId:
    process.env.NEXT_PUBLIC_APPWRITE_APPLICATIONS_TABLE_ID ?? "application_cards",
  resumesTableId:
    process.env.NEXT_PUBLIC_APPWRITE_RESUMES_TABLE_ID ?? "resumes",
  resumeVersionsTableId:
    process.env.NEXT_PUBLIC_APPWRITE_RESUME_VERSIONS_TABLE_ID ?? "resume_versions",
  resumeMessagesTableId:
    process.env.NEXT_PUBLIC_APPWRITE_RESUME_MESSAGES_TABLE_ID ?? "resume_messages",
  profileFactsTableId:
    process.env.NEXT_PUBLIC_APPWRITE_PROFILE_FACTS_TABLE_ID ?? "profile_facts",
  factBulletsTableId:
    process.env.NEXT_PUBLIC_APPWRITE_FACT_BULLETS_TABLE_ID ?? "fact_bullets",
  agentJobsTableId:
    process.env.NEXT_PUBLIC_APPWRITE_AGENT_JOBS_TABLE_ID ?? "agent_jobs",
  templatesTableId:
    process.env.NEXT_PUBLIC_APPWRITE_TEMPLATES_TABLE_ID ?? "templates",
  resumeFilesBucketId:
    process.env.NEXT_PUBLIC_APPWRITE_RESUME_FILES_BUCKET_ID ?? "resume_files",
  agentFunctionId:
    process.env.NEXT_PUBLIC_APPWRITE_AGENT_FUNCTION_ID ?? "job-os-agents",
};

export type PipelineBackend = "legacy" | "appwrite";
export type WorkspaceBackend = "legacy" | "appwrite";

export const pipelineBackend: PipelineBackend =
  process.env.NEXT_PUBLIC_PIPELINE_BACKEND === "appwrite" ? "appwrite" : "legacy";

export const isAppwritePipelineEnabled = pipelineBackend === "appwrite";

export const workspaceBackend: WorkspaceBackend =
  process.env.NEXT_PUBLIC_WORKSPACE_BACKEND === "appwrite"
    ? "appwrite"
    : "legacy";

export const isAppwriteWorkspaceEnabled = workspaceBackend === "appwrite";

export const isAppwriteInteractiveBackendEnabled =
  isAppwritePipelineEnabled && isAppwriteWorkspaceEnabled;

export function requirePublicAppwriteConfig() {
  const missing = Object.entries(appwriteConfig)
    .filter(([, value]) => !value)
    .map(([key]) => key);

  if (missing.length > 0) {
    throw new Error(`Appwrite is enabled but missing: ${missing.join(", ")}`);
  }

  return appwriteConfig;
}
