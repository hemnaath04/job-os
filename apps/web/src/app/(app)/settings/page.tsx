import { Settings } from "lucide-react";
import { ComingSoon } from "@/components/coming-soon";

export default function SettingsPage() {
  return (
    <ComingSoon
      icon={Settings}
      milestone="later"
      title="Settings"
      description="Theme, default resume template, API token management, and integration toggles."
    />
  );
}
