import { useState, useEffect } from "react";

/**
 * Returns a debounced version of `value` that only updates after `delay` ms
 * of inactivity. Cleans up the timer on unmount or when deps change.
 */
export function useDebounce(value, delay = 350) {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);

  return debouncedValue;
}
