import { useEffect, useState } from "react";

/**
 * Debounces a value so a live preview re-renders once typing pauses, not on
 * every keystroke -- each change behind a resume preview is a real backend
 * LaTeX/Tectonic compile, not a free client-side reflow. Initializes to the
 * live value, so switching a view on without having just typed anything sees
 * no artificial delay: the debounced value already equals the live one.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
