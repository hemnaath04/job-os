export type NavItem = {
  href: string;
  title: string;
  description: string;
};

export type NavGroup = {
  label: string;
  items: NavItem[];
};

export const NAV: NavGroup[] = [
  {
    label: "Get started",
    items: [
      {
        href: "/docs",
        title: "Introduction",
        description: "What job.os does, and how the pieces fit together.",
      },
      {
        href: "/docs/quickstart",
        title: "Quick start",
        description: "Verify your facts, find or add a job, tailor a resume, track it.",
      },
    ],
  },
  {
    label: "Pipeline",
    items: [
      {
        href: "/docs/dashboard",
        title: "Dashboard",
        description: "Where the search stands.",
      },
      {
        href: "/docs/applications",
        title: "Applications",
        description: "The pipeline, as a board or a table.",
      },
      {
        href: "/docs/jobs",
        title: "Job Finder",
        description: "Search, don't just browse.",
      },
      {
        href: "/docs/tailor",
        title: "AI Resume Tailor",
        description: "Tuned to the posting, not invented for it.",
      },
      {
        href: "/docs/interview",
        title: "Interview Prep",
        description: "Rehearse your own resume, not just the role.",
      },
      {
        href: "/docs/calendar",
        title: "Calendar",
        description: "The follow-ups you'd otherwise forget.",
      },
    ],
  },
  {
    label: "Documents",
    items: [
      {
        href: "/docs/resumes",
        title: "Resume studio",
        description: "One master, many tailored versions.",
      },
      {
        href: "/docs/cover-letters",
        title: "Cover Letters",
        description: "Every sentence cites a bullet.",
      },
      {
        href: "/docs/profile",
        title: "Career profile",
        description: "The one vault everything cites.",
      },
    ],
  },
  {
    label: "Other",
    items: [
      {
        href: "/docs/settings",
        title: "Settings",
        description: "Set your defaults once.",
      },
      {
        href: "/docs/mcp",
        title: "MCP connector",
        description: "Connect Claude Code or any MCP client to your own job.os data.",
      },
    ],
  },
];

export const FLAT_NAV: NavItem[] = NAV.flatMap((g) => g.items);

export function surroundingPages(href: string): { prev?: NavItem; next?: NavItem } {
  const i = FLAT_NAV.findIndex((item) => item.href === href);
  if (i === -1) return {};
  return { prev: FLAT_NAV[i - 1], next: FLAT_NAV[i + 1] };
}
