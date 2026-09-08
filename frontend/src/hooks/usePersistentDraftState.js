import { useCallback, useEffect, useMemo, useRef, useState } from "react";

function getStorageKey(key, version) {
  return `renest:draft:${key}:v${version}`;
}

function canUseStorage() {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

function safeReadDraft(storageKey) {
  if (!canUseStorage()) {
    return null;
  }

  try {
    const raw = window.localStorage.getItem(storageKey);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/**
 * Form state that survives a reload, autosaved to localStorage.
 *
 * Callers pass `serialize`/`deserialize` as inline arrow functions, so those
 * props change identity on every render. Everything derived from them is
 * therefore keyed on the serialized *value* (a JSON string), never on object
 * identity: a previous version depended on the freshly-built object and wrote a
 * new `lastSavedAt` on each run, so the effect re-triggered itself in a render
 * loop bounded only by wall-clock time. That showed up as a per-keystroke
 * localStorage write storm in the browser and as a 5-second test timeout under
 * load in CI.
 */
export function usePersistentDraftState({
  key,
  initialValue,
  enabled = true,
  version = 1,
  serialize = (value) => value,
  deserialize = (storedValue, fallback) => ({ ...fallback, ...storedValue }),
} = {}) {
  const storageKey = useMemo(() => getStorageKey(key, version), [key, version]);

  // Held in refs so an inline arrow prop cannot invalidate memos or effects.
  const serializeRef = useRef(serialize);
  const deserializeRef = useRef(deserialize);
  serializeRef.current = serialize;
  deserializeRef.current = deserialize;

  const initialValueRef = useRef(initialValue);
  initialValueRef.current = initialValue;

  const hydratedKeyRef = useRef("");
  const [state, setState] = useState(initialValue);
  const [lastSavedAt, setLastSavedAt] = useState(null);
  const [restoredAt, setRestoredAt] = useState(null);

  const serializedState = useMemo(() => serializeRef.current(state), [state]);
  const serializedJson = useMemo(() => JSON.stringify(serializedState), [serializedState]);
  // Captured once: the pristine form the draft is compared against.
  const initialJsonRef = useRef(null);
  if (initialJsonRef.current === null) {
    initialJsonRef.current = JSON.stringify(serializeRef.current(initialValue));
  }
  const isDirty = serializedJson !== initialJsonRef.current;

  useEffect(() => {
    if (!enabled) {
      return;
    }

    if (hydratedKeyRef.current === storageKey) {
      return;
    }

    hydratedKeyRef.current = storageKey;
    const stored = safeReadDraft(storageKey);

    if (!stored?.data) {
      setState(initialValueRef.current);
      setLastSavedAt(null);
      setRestoredAt(null);
      return;
    }

    setState(deserializeRef.current(stored.data, initialValueRef.current));
    setLastSavedAt(stored.savedAt || null);
    setRestoredAt(Date.now());
  }, [enabled, storageKey]);

  const clearDraft = useCallback(
    ({ reset = false } = {}) => {
      if (canUseStorage()) {
        window.localStorage.removeItem(storageKey);
      }
      setLastSavedAt(null);
      setRestoredAt(null);
      if (reset) {
        setState(initialValueRef.current);
      }
    },
    [storageKey],
  );

  // Autosave. Keyed on the serialized JSON, so it runs when the form's content
  // actually changes and not merely because a prop was re-created.
  useEffect(() => {
    if (!enabled || !canUseStorage()) {
      return;
    }

    if (!isDirty) {
      window.localStorage.removeItem(storageKey);
      setLastSavedAt((current) => (current === null ? current : null));
      return;
    }

    const savedAt = new Date().toISOString();
    try {
      window.localStorage.setItem(
        storageKey,
        JSON.stringify({ savedAt, data: JSON.parse(serializedJson) }),
      );
      setLastSavedAt(savedAt);
    } catch {
      // Ignore quota/storage errors so the form stays fully usable.
    }
  }, [enabled, isDirty, serializedJson, storageKey]);

  return {
    state,
    setState,
    isDirty,
    lastSavedAt,
    restoredAt,
    hasRestoredDraft: Boolean(restoredAt),
    clearDraft,
  };
}
