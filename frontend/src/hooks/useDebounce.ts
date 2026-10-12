import { useEffect, useState } from "react";

/**
 * Returns a debounced version of `value` that only updates after `delay` ms
 * of inactivity. Cleans up the timer on unmount or when deps change.
 */
export function useDebounce<T>(value: T, delay = 350): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);

  return debouncedValue;
}
