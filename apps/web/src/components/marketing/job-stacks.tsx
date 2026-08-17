"use client";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Bookmark } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { SPRING_LAYOUT } from "@/lib/ease";

interface StackCard {
  id: string;
  fit: number;
  role: string;
  company: string;
  domain: string | null;
  team: string;
}

/**
 * Three decks of plausible postings, not one. A single static card says
 * "here is a job"; three decks quietly cycling say "there is always another
 * one" -- the actual pitch of a discovery feed, without a line of copy.
 */
const DECKS: StackCard[][] = [
  [
    { id: "a1", fit: 92, role: "Backend Engineer, New Grad", company: "Anthropic", domain: "anthropic.com", team: "AI Research" },
    { id: "a2", fit: 78, role: "Platform Engineer Intern", company: "Stripe", domain: "stripe.com", team: "Infrastructure" },
    { id: "a3", fit: 85, role: "Backend Engineer II", company: "Notion", domain: "notion.so", team: "Core Product" },
  ],
  [
    { id: "b1", fit: 88, role: "Generative AI Lead", company: "Perplexity", domain: "perplexity.ai", team: "AI Platform" },
    { id: "b2", fit: 74, role: "Multimodal AI Lead", company: "Google", domain: "google.com", team: "DeepMind" },
    { id: "b3", fit: 81, role: "ML Engineer", company: "Perplexity", domain: "perplexity.ai", team: "Infrastructure" },
  ],
  [
    { id: "c1", fit: 90, role: "LLM Platform Engineer", company: "Google", domain: "google.com", team: "Engineering" },
    { id: "c2", fit: 71, role: "Research Engineer", company: "Google", domain: "google.com", team: "Research" },
    { id: "c3", fit: 83, role: "Infra Engineer, ML Serving", company: "Datadog", domain: "datadoghq.com", team: "Platform" },
  ],
];

const ROTATE_MS = 4200;
/** Each column starts its own clock offset, so all three never flip at once
 * -- three decks changing in lockstep would read as one thing blinking. */
const COLUMN_STAGGER_MS = 1300;

function DeckLogo({ domain, name }: { domain: string | null; name: string }) {
  const [failed, setFailed] = useState(false);
  if (!domain || failed) {
    const initials = name.slice(0, 2).toUpperCase();
    return (
      <span className="grid size-8 shrink-0 place-items-center rounded-full bg-[color:var(--color-mint-ink)]/12 text-[10px] font-semibold text-[color:var(--color-mint-ink)]">
        {initials}
      </span>
    );
  }
  return (
    <img
      src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`}
      alt=""
      width={32}
      height={32}
      className="size-8 shrink-0 rounded-full bg-[color:var(--color-surface-2)] object-contain p-1.5"
      onError={() => setFailed(true)}
    />
  );
}

function DeckCard({ card }: { card: StackCard }) {
  return (
    <div className="flex h-[13.5rem] w-full flex-col justify-between rounded-[var(--radius-card-lg)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] p-5 shadow-[var(--shadow-glass)]">
      <div className="flex items-start justify-between gap-3">
        <span className="rounded-full border border-[color:var(--color-mint-ink)]/35 bg-[color:var(--color-mint-ink)]/10 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-[color:var(--color-mint-ink)]">
          {card.fit}% fit
        </span>
        <Bookmark className="size-4 shrink-0 text-[color:var(--color-text-dim)]" aria-hidden="true" />
      </div>

      <div className="flex items-start justify-between gap-3">
        <p className="text-lg font-medium leading-tight tracking-[-0.01em] text-[color:var(--color-text)]">
          {card.role}
        </p>
        {/* Decorative only -- a real menu here would open onto nothing, since
            this card has no job behind it. */}
        <span aria-hidden="true" className="mt-0.5 flex shrink-0 flex-col items-center gap-1">
          <span className="h-3.5 w-[3px] rounded-full bg-[color:var(--color-text-dim)]/50" />
          <span className="size-1 rounded-full bg-[color:var(--color-text-dim)]/40" />
          <span className="size-1 rounded-full bg-[color:var(--color-text-dim)]/40" />
        </span>
      </div>

      <div className="flex items-center justify-between gap-3 border-t border-[color:var(--color-border)] pt-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <DeckLogo domain={card.domain} name={card.company} />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-[color:var(--color-text)]">{card.company}</p>
            <p className="truncate text-xs text-[color:var(--color-text-dim)]">{card.team}</p>
          </div>
        </div>
        <Link
          href="/sign-up"
          className="shrink-0 rounded-full bg-[color:var(--color-text)] px-4 py-1.5 text-xs font-medium text-[color:var(--color-bg)] transition hover:opacity-85"
        >
          View
        </Link>
      </div>
    </div>
  );
}

function JobDeck({ cards, columnIndex }: { cards: StackCard[]; columnIndex: number }) {
  const reduce = useReducedMotion();
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (reduce || paused || cards.length < 2) return;
    let interval: number | undefined;
    const start = window.setTimeout(() => {
      interval = window.setInterval(() => {
        setIndex((current) => (current + 1) % cards.length);
      }, ROTATE_MS);
    }, columnIndex * COLUMN_STAGGER_MS);
    return () => {
      window.clearTimeout(start);
      if (interval) window.clearInterval(interval);
    };
  }, [cards.length, columnIndex, paused, reduce]);

  const front = cards[index];

  return (
    <div
      className="relative"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      {/* Ghost cards give the deck its depth; they never change, only the
          front face does, so the stack reads as "more behind this one"
          rather than something itself in motion. */}
      <div
        aria-hidden="true"
        className="absolute inset-x-3 top-3 h-[13.5rem] rounded-[var(--radius-card-lg)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-2)] opacity-60"
      />
      <div
        aria-hidden="true"
        className="absolute inset-x-1.5 top-1.5 h-[13.5rem] rounded-[var(--radius-card-lg)] border border-[color:var(--color-border)] bg-[color:var(--color-surface-1)] opacity-80"
      />
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={front.id}
          initial={reduce ? undefined : { opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={reduce ? undefined : { opacity: 0, y: -6 }}
          transition={reduce ? { duration: 0 } : SPRING_LAYOUT}
          className="relative"
        >
          <DeckCard card={front} />
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

/**
 * Three decks of job matches, each quietly cycling through a few postings.
 * Replaces a single static example with the actual shape of the product: not
 * one job, a feed of them, each scored against the resume rather than priced
 * by the hour.
 */
export function JobStacks() {
  return (
    <div className="animate-rise-in mt-20 grid w-full max-w-4xl grid-cols-1 gap-6 pt-3 sm:grid-cols-3">
      {DECKS.map((cards, columnIndex) => (
        <JobDeck key={columnIndex} cards={cards} columnIndex={columnIndex} />
      ))}
    </div>
  );
}
