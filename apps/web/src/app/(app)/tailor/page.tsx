import { Sparkles } from "lucide-react";
import { ComingSoon } from "@/components/coming-soon";

export default function TailorPage() {
  return (
    <ComingSoon
      icon={Sparkles}
      milestone="M3 — next"
      title="Resume tailoring"
      description="Pick a job from your applications, and Claude Opus 4.8 rewrites bullets to match the JD — citing every fact it used, asking you about anything missing instead of inventing it."
      cta={{ href: "/applications", label: "Open Applications" }}
    />
  );
}
