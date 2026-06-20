import { Radar } from "lucide-react";
import { ComingSoon } from "@/components/coming-soon";

export default function DiscoverPage() {
  return (
    <ComingSoon
      icon={Radar}
      milestone="M4"
      title="Job discovery feed"
      description="Daily-refreshed feed via TheirStack + SimplifyJobs/Pitt CSC GitHub crawl + per-company Greenhouse/Lever scrapers. Ranked by fit against your profile."
      cta={{ href: "/applications", label: "Open Applications" }}
    />
  );
}
