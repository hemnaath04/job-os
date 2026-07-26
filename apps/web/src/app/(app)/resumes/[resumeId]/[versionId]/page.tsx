import ResumeEditorClient from "./resume-editor-client";

export default async function ResumeEditorPage({
  params,
}: {
  params: Promise<{ resumeId: string; versionId: string }>;
}) {
  const { resumeId, versionId } = await params;
  return <ResumeEditorClient resumeId={resumeId} versionId={versionId} />;
}
