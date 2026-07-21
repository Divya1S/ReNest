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

function valuesMatch(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function usePersistentDraftState({
  key,
  initialValue,
  enabled = true,
  version = 1,
  serialize = (value) => value,
  deserialize = (storedValue, fallback) => ({ ...fallback, ...storedValue }),
} = {}) {
  const storageKey = useMemo(() => getStorageKey(key, version), [key, version]);
  const initialSnapshot = useMemo(() => serialize(initialValue), [initialValue, serialize]);
  const initialSnapshotRef = useRef(initialSnapshot);
  const hydratedKeyRef = useRef("");
  const [state, setState] = useState(initialValue);
  const [lastSavedAt, setLastSavedAt] = useState(null);
  const [restoredAt, setRestoredAt] = useState(null);

  useEffect(() => {
    initialSnapshotRef.current = initialSnapshot;
  }, [initialSnapshot]);

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
      setState(initialValue);
      setLastSavedAt(null);
      setRestoredAt(null);
      return;
    }

    setState(deserialize(stored.data, initialValue));
    setLastSavedAt(stored.savedAt || null);
    setRestoredAt(Date.now());
  }, [deserialize, enabled, initialValue, storageKey]);

  const clearDraft = useCallback(
    ({ reset = false } = {}) => {
      if (canUseStorage()) {
        window.localStorage.removeItem(storageKey);
      }
      setLastSavedAt(null);
      setRestoredAt(null);
      if (reset) {
        setState(initialValue);
      }
    },
    [initialValue, storageKey],
  );

  const serializedState = useMemo(() => serialize(state), [serialize, state]);
  const isDirty = !valuesMatch(serializedState, initialSnapshotRef.current);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    if (!canUseStorage()) {
      return;
    }

    if (!isDirty) {
      window.localStorage.removeItem(storageKey);
      setLastSavedAt(null);
      return;
    }

    const savedAt = new Date().toISOString();
    try {
      window.localStorage.setItem(
        storageKey,
        JSON.stringify({
          savedAt,
          data: serializedState,
        }),
      );
      setLastSavedAt(savedAt);
    } catch {
      // Ignore quota/storage errors so the form stays fully usable.
    }
  }, [enabled, isDirty, serializedState, storageKey]);

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
