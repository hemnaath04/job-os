"use client";

import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import Link from "next/link";
import type { Route } from "next";

export function EmptyState({
  icon: Icon,
  title,
  description,
  cta,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  cta?: { href: Route; label: string };
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="glass mt-8 rounded-[var(--radius-card-lg)] p-10 text-center"
    >
      <div className="relative mx-auto flex size-14 items-center justify-center rounded-2xl bg-gradient-brand text-white shadow-[var(--shadow-brand-glow)]">
        <div className="absolute inset-0 rounded-2xl bg-gradient-to-br from-white/20 to-transparent" />
        <Icon className="relative size-6" />
      </div>
      <h3 className="mt-5 text-lg font-medium tracking-tight">{title}</h3>
      <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-[color:var(--color-text-muted)]">
        {description}
      </p>
      {cta && (
        <Link
          href={cta.href}
          className="mt-6 inline-flex items-center gap-1.5 rounded-full bg-gradient-brand px-4 py-2 text-sm font-medium text-white shadow-[var(--shadow-brand-glow)] transition hover:scale-[1.02]"
        >
          {cta.label}
        </Link>
      )}
    </motion.div>
  );
}
