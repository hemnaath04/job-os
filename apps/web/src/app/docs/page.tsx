import {
  BookOpenText,
  Briefcase,
  CalendarDays,
  FileSignature,
  FileText,
  LayoutDashboard,
  MessageSquareText,
  Radar,
  Settings as SettingsIcon,
  Sparkles,
  UserSquare2,
} from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { MarketingNav } from "@/components/marketing/marketing-nav";
import {
  ApplicationsMock,
  CalendarMock,
  CoverLettersMock,
  DashboardMock,
  InterviewMock,
  JobsMock,
  ProfileMock,
  ResumesMock,
  SettingsMock,
  TailorMock,
} from "./_mocks";

export const metadata: Metadata = {
  title: "Docs | job.os",
  description: "What each part of job.os does, and how the pieces fit together.",
};

type Section = {
  id: string;
  icon: React.ReactNode;
  title: string;
  eyebrow: string;
  body: React.ReactNode;
  mock: React.ReactNode;
};

const SECTIONS: Section[] = [
  {
    id: "dashboard",
    icon: <LayoutDashboard className="size-4" />,
    eyebrow: "Dashboard",
    title: "Where the search stands",
    body: (
      <>
        <p>
          Total applications, response rate, interview conversion, and offers, with a
          week-over-week delta on the number that moves fastest. A daily activity chart and a
          pipeline-progress gauge sit underneath, so a lull is visible before it becomes a
          problem.
        </p>
        <p>
          First time here with nothing tracked yet, you get a plain &ldquo;add your first
          application&rdquo; prompt instead of an empty chart. If the data fails to load, the
          panel says so and gives you a retry, rather than pretending the count is zero.
        </p>
      </>
    ),
    mock: <DashboardMock />,
  },
  {
    id: "applications",
    icon: <Briefcase className="size-4" />,
    eyebrow: "Applications",
    title: "The pipeline, as a board or a table",
    body: (
      <>
        <p>
          Kanban by default: Wishlist, Applied, Interview, Rejected, Offer. Drag a card to change
          its status, or work the same data as a sortable table when you want the exact status
          (Applied vs. OA received vs. Rejected vs. Withdrawn) instead of the merged column view.
        </p>
        <p>
          Double-click a card to open the original posting. &ldquo;Tailor&rdquo; jumps straight
          into the resume tailor for that job. Archiving is undo-able from the confirmation toast,
          nothing is deleted outright.
        </p>
      </>
    ),
    mock: <ApplicationsMock />,
  },
  {
    id: "jobs",
    icon: <Radar className="size-4" />,
    eyebrow: "Job Finder",
    title: "Search, don&rsquo;t just browse",
    body: (
      <>
        <p>
          Type a plain-English query like &ldquo;fullstack intern in Boston, last two weeks&rdquo;
          and an agent turns it into structured filters, or set the filters yourself and pick
          which sources to search (the free boards run by default, add a key for wider coverage).
        </p>
        <p>
          Every result carries a fit score computed against your verified profile, plus any
          eligibility flags the posting text implies. One click either imports a job to your
          Wishlist, or imports it and sends you straight into tailoring for it.
        </p>
      </>
    ),
    mock: <JobsMock />,
  },
  {
    id: "tailor",
    icon: <Sparkles className="size-4" />,
    eyebrow: "AI Resume Tailor",
    title: "Tuned to the posting, not invented for it",
    body: (
      <>
        <p>
          Point it at a job and it iterates: draft, score against the posting, revise, until it
          clears the match target or runs out of requirements your verified profile can actually
          back. Every version keeps its ATS score and QA review score, so you can see the trail
          that got you there.
        </p>
        <p>
          A run takes a little while, since the same call fetches the posting and asks the model
          to write. The progress bar tracks real stage updates when they arrive, and falls back to
          a typical-run-timing estimate (labeled as such) when they don&rsquo;t, so it never reads
          as frozen.
        </p>
      </>
    ),
    mock: <TailorMock />,
  },
  {
    id: "interview",
    icon: <MessageSquareText className="size-4" />,
    eyebrow: "Interview Prep",
    title: "Rehearse your own resume, not just the role",
    body: (
      <>
        <p>
          Generates a prep pack from the job, your tailored resume, and your verified profile:
          questions about your own bullets (the category almost nobody rehearses), technical,
          behavioural, and questions to ask them. Each answer is a STAR scaffold built only from
          evidence you&rsquo;ve verified.
        </p>
        <p>
          A Readiness score (0-100) is computed from must-have topic coverage, kept separate from
          the model&rsquo;s own self-estimate so the two are never confused for one grade.
        </p>
      </>
    ),
    mock: <InterviewMock />,
  },
  {
    id: "resumes",
    icon: <FileText className="size-4" />,
    eyebrow: "Resume studio",
    title: "One master, many tailored versions",
    body: (
      <>
        <p>
          Your source resumes hold the data (experience, skills); templates hold the look
          (LaTeX only, never your data). Tailoring combines the two and saves the result as a new
          version under the source resume, the template itself never changes.
        </p>
        <p>
          Upload a `.tex` file to keep a design exactly, or a PDF to have it reverse-engineered
          into one (described honestly in the UI as &ldquo;comes close,&rdquo; not a promise of a
          pixel match).
        </p>
      </>
    ),
    mock: <ResumesMock />,
  },
  {
    id: "cover-letters",
    icon: <FileSignature className="size-4" />,
    eyebrow: "Cover Letters",
    title: "Every sentence cites a bullet",
    body: (
      <>
        <p>
          Pick a job and a tone (plain, warm, or direct, never &ldquo;enthusiastic&rdquo;), and
          it writes two passes, the second only runs if the first left something worth fixing.
          Every claim shows the bullet it came from inline.
        </p>
        <p>
          What the profile can&rsquo;t back becomes an open question, shown next to the letter,
          not a sentence it quietly made up. Anything it drafted and then cut for lack of evidence
          is listed too, struck through, with the reason.
        </p>
      </>
    ),
    mock: <CoverLettersMock />,
  },
  {
    id: "profile",
    icon: <UserSquare2 className="size-4" />,
    eyebrow: "Career profile",
    title: "The one vault everything cites",
    body: (
      <>
        <p>
          Your career facts (roles, projects, education, skills, certifications) each carry
          bullets, and every resume, cover letter, and interview answer job.os generates is only
          allowed to cite what&rsquo;s verified here. Nothing in the vault means nothing gets
          claimed.
        </p>
        <p>
          Add facts by hand or upload a resume and let the extractor pull them out, it reports how
          many were new versus already on file, so nothing gets silently duplicated.
        </p>
      </>
    ),
    mock: <ProfileMock />,
  },
  {
    id: "calendar",
    icon: <CalendarDays className="size-4" />,
    eyebrow: "Calendar",
    title: "The follow-ups you&rsquo;d otherwise forget",
    body: (
      <>
        <p>
          Not a full scheduler, a next-action timeline: Overdue, Today, This week, Later, built
          from the next-action date on each application. Click through to the application to act
          on it.
        </p>
      </>
    ),
    mock: <CalendarMock />,
  },
  {
    id: "settings",
    icon: <SettingsIcon className="size-4" />,
    eyebrow: "Settings",
    title: "Set your defaults once",
    body: (
      <>
        <p>
          Target roles and seniority range, work authorization (so ineligible roles get filtered
          out rather than wasting your time), default discovery filters, salary floor, target and
          excluded companies, and your timezone. Everything here seeds Job Finder and the tailor
          so you&rsquo;re not re-entering it per search.
        </p>
      </>
    ),
    mock: <SettingsMock />,
  },
];

export default function DocsPage() {
  return (
    <main className="relative isolate min-h-screen overflow-x-hidden">
      <MarketingNav />

      <section className="relative px-6 pb-16 pt-32 md:pt-40">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute left-1/2 top-[4rem] -z-10 h-[26rem] w-[min(60rem,120vw)] -translate-x-1/2 blur-[110px]"
          style={{
            background:
              "radial-gradient(50% 50% at 50% 50%, rgba(255,231,135,0.22) 0%, rgba(248,214,79,0.10) 42%, transparent 72%)",
          }}
        />
        <div className="mx-auto max-w-3xl text-center">
          <p className="inline-flex items-center gap-2 rounded-full border border-[color:var(--color-border-strong)] bg-[color:var(--color-surface-2)] px-4 py-1.5 text-xs text-[color:var(--color-text-muted)] backdrop-blur-sm">
            <BookOpenText className="size-3.5" /> Docs
          </p>
          <h1 className="text-gradient-night mt-6 text-balance text-4xl font-medium leading-[1.08] tracking-[-0.04em] md:text-5xl">
            What job.os does, page by page
          </h1>
          <p className="mt-5 text-pretty text-base leading-relaxed text-[color:var(--color-text-muted)]">
            One workspace: a pipeline board, a resume tailor, and a discovery feed, all reading
            from a single vault of career facts you&rsquo;ve verified yourself. Here&rsquo;s what
            each part does and how they connect.
          </p>
        </div>
      </section>

      <section className="mx-auto max-w-3xl px-6">
        <div className="rounded-2xl border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)]/60 p-6 backdrop-blur-sm">
          <h2 className="text-sm font-medium uppercase tracking-wide text-[color:var(--color-text-dim)]">
            Quick start
          </h2>
          <ol className="mt-4 space-y-3 text-sm leading-relaxed text-[color:var(--color-text-muted)]">
            <li>
              <span className="font-medium text-[color:var(--color-text)]">1. Verify your facts.</span>{" "}
              Upload a resume on the <Link href="/profile" className="underline underline-offset-2">Profile</Link> page,
              or add facts by hand. This is the only source every generated document is allowed to
              cite.
            </li>
            <li>
              <span className="font-medium text-[color:var(--color-text)]">2. Find or add a job.</span>{" "}
              Search on <Link href="/jobs" className="underline underline-offset-2">Job Finder</Link>, or paste a
              URL or description straight into <Link href="/applications" className="underline underline-offset-2">Applications</Link>.
            </li>
            <li>
              <span className="font-medium text-[color:var(--color-text)]">3. Tailor a resume for it.</span>{" "}
              One click from either page opens the <Link href="/tailor" className="underline underline-offset-2">Resume Tailor</Link>,
              which iterates until it hits the match target or runs out of ground your profile
              covers.
            </li>
            <li>
              <span className="font-medium text-[color:var(--color-text)]">4. Track it, then prep.</span>{" "}
              Move the card as things progress, and generate an{" "}
              <Link href="/interview" className="underline underline-offset-2">Interview Prep</Link> pack once you
              have a screen on the calendar.
            </li>
          </ol>
        </div>
      </section>

      <section className="mx-auto max-w-5xl px-6 py-24">
        <div className="space-y-20">
          {SECTIONS.map((s, i) => (
            <div
              key={s.id}
              id={s.id}
              className="grid scroll-mt-24 grid-cols-1 items-center gap-10 md:grid-cols-2"
            >
              <div className={i % 2 === 1 ? "md:order-2" : undefined}>
                <span className="inline-flex items-center gap-2 rounded-full border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] px-3 py-1 text-xs text-[color:var(--color-text-muted)]">
                  {s.icon}
                  {s.eyebrow}
                </span>
                <h2 className="mt-4 text-2xl font-medium tracking-[-0.02em]">{s.title}</h2>
                <div className="mt-3 space-y-3 text-sm leading-relaxed text-[color:var(--color-text-muted)]">
                  {s.body}
                </div>
              </div>
              <div className={i % 2 === 1 ? "md:order-1" : undefined}>{s.mock}</div>
            </div>
          ))}
        </div>
      </section>

      <section id="honest" className="mx-auto max-w-3xl px-6 pb-28 text-center">
        <h2 className="text-balance text-3xl font-medium tracking-[-0.035em] md:text-4xl">
          Why every page enforces the same rule
        </h2>
        <p className="mx-auto mt-5 max-w-xl text-pretty text-base leading-relaxed text-[color:var(--color-text-muted)]">
          The tailor, the cover-letter writer, and interview prep all draw from the exact same
          verified profile. None of them can invent a skill or a number that isn&rsquo;t backed by
          a bullet you added yourself, if the evidence isn&rsquo;t there, the UI raises a gap
          question instead of quietly filling it in.
        </p>
      </section>

      <footer className="border-t border-[color:var(--color-border)] px-6 py-8">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-3 text-xs text-[color:var(--color-text-dim)] sm:flex-row">
          <span className="font-mono">job.os</span>
          <span>Your data stays attached to your own account.</span>
        </div>
      </footer>
    </main>
  );
}
