"use client";

/** Company avatar: shows the real company logo (via favicon lookup on the
 * company domain) and falls back to a deterministic gradient initial when
 * there is no domain or the logo fails to load. */

import { useState } from "react";

const PALETTE = [
  ["#6366F1", "#8B5CF6"],
  ["#06B6D4", "#3B82F6"],
  ["#A855F7", "#EC4899"],
  ["#10B981", "#5EEAD4"],
  ["#F59E0B", "#F5B544"],
  ["#F43F5E", "#FF6B8A"],
];

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

function cleanDomain(domain?: string | null): string | null {
  if (!domain) return null;
  const d = domain
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/\/.*$/, "")
    .replace(/^www\./i, "")
    .toLowerCase();
  return d.includes(".") ? d : null;
}

export function CompanyAvatar({
  name,
  domain,
  size = 28,
  className = "",
}: {
  name: string;
  domain?: string | null;
  size?: number;
  className?: string;
}) {
  const d = cleanDomain(domain);
  const [failed, setFailed] = useState(false);

  if (d && !failed) {
    return (
      <div
        className={`relative flex shrink-0 items-center justify-center overflow-hidden rounded-lg border border-[color:var(--color-border)] bg-white ${className}`}
        style={{ width: size, height: size }}
      >
        {/* Plain img (not next/image) keeps external favicons config-free. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(d)}&sz=64`}
          alt={`${name} logo`}
          width={size}
          height={size}
          loading="lazy"
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
          className="size-full object-contain"
          style={{ padding: Math.max(2, Math.round(size * 0.13)) }}
        />
      </div>
    );
  }

  const letter = (name?.trim()?.[0] ?? "?").toUpperCase();
  const [from, to] = PALETTE[hash(name || "?") % PALETTE.length];
  return (
    <div
      className={`relative flex shrink-0 items-center justify-center overflow-hidden rounded-lg font-semibold text-white ${className}`}
      style={{
        width: size,
        height: size,
        fontSize: size * 0.45,
        background: `linear-gradient(135deg, ${from}, ${to})`,
        boxShadow: `0 0 18px -6px ${from}`,
      }}
      aria-hidden
    >
      <div className="absolute inset-0 bg-gradient-to-br from-[color:var(--color-cream)]/40 to-transparent" />
      <span className="relative">{letter}</span>
    </div>
  );
}
