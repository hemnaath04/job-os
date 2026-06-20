import { CalendarDays } from "lucide-react";
import { ComingSoon } from "@/components/coming-soon";

export default function CalendarPage() {
  return (
    <ComingSoon
      icon={CalendarDays}
      milestone="M2.5"
      title="Calendar view"
      description="OA deadlines, interview slots, and application due dates over a month view. Pulls next_action_at across every application."
      cta={{ href: "/applications", label: "Open Applications" }}
    />
  );
}
