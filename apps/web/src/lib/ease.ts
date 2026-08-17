import type { Transition } from "framer-motion";

/** Layout/position changes — a folder opening, a card settling into place. */
export const SPRING_LAYOUT: Transition = {
  type: "spring",
  stiffness: 380,
  damping: 32,
  mass: 0.9,
};

/** A press/tap response — snappy, not bouncy. */
export const SPRING_PRESS: Transition = {
  type: "spring",
  stiffness: 500,
  damping: 30,
};
