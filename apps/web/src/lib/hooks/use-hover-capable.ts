import { useEffect, useState } from "react";

/**
 * Whether the pointer can hover at all — a mouse/trackpad, not a touchscreen.
 * A touch device has no hover state to open on, so a component that opens on
 * pointer-enter must fall back to tap-to-open instead of never opening.
 */
export function useHoverCapable(): boolean {
  const [canHover, setCanHover] = useState(true);

  useEffect(() => {
    const query = window.matchMedia("(hover: hover) and (pointer: fine)");
    setCanHover(query.matches);
    const onChange = (event: MediaQueryListEvent) => setCanHover(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return canHover;
}
