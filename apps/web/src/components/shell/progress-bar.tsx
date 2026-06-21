"use client";

import { useIsFetching, useIsMutating } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";

/**
 * Top-of-viewport progress indicator. Visible whenever any TanStack Query
 * is fetching OR any mutation is in flight. We can't get true percent from
 * Claude (Anthropic doesn't stream progress events), so the bar uses a
 * smooth pseudo-progress curve that creeps toward 90% and then completes
 * when the work finishes — same trick NProgress + Vercel + Linear use.
 */
export function TopProgressBar() {
  const fetching = useIsFetching();
  const mutating = useIsMutating();
  const active = fetching + mutating > 0;

  const [pct, setPct] = useState(0);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (active) {
      setVisible(true);
      setPct(8); // jump in fast so the user sees movement immediately
      const interval = setInterval(() => {
        setPct((p) => {
          if (p >= 90) return p; // park at 90 until the call resolves
          // Diminishing-returns ramp — feels faster early, slower as it
          // approaches the ceiling. Models the "Claude is thinking…" wait.
          const remaining = 90 - p;
          return p + Math.max(0.5, remaining * 0.06);
        });
      }, 250);
      return () => clearInterval(interval);
    }

    // No work in flight — snap to 100, then fade out.
    setPct(100);
    const t = setTimeout(() => {
      setVisible(false);
      setPct(0);
    }, 400);
    return () => clearTimeout(t);
  }, [active]);

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="fixed left-0 right-0 top-0 z-50 h-[2px] bg-transparent"
        >
          <motion.div
            className="h-full rounded-r-full"
            style={{
              background:
                "linear-gradient(90deg, #CCFF00 0%, #DFFF00 50%, #FFFF00 100%)",
              boxShadow: "0 0 18px rgba(204,255,0,0.6)",
            }}
            animate={{ width: `${pct}%` }}
            transition={{ duration: 0.25, ease: "easeOut" }}
          />
        </motion.div>
      )}
    </AnimatePresence>
  );
}
