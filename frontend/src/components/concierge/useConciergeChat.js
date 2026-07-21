import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "../../lib/api";

export const ERROR_BUBBLE =
  "I couldn't get a reply just now — your message wasn't lost on our side, so give it another try in a moment.";

function getCookie(name) {
  const cookie = document.cookie.split("; ").find((part) => part.startsWith(`${name}=`));
  return cookie ? decodeURIComponent(cookie.split("=")[1]) : "";
}

/**
 * Shared conversation state for the Nest Concierge.
 *
 * Both surfaces — the floating widget and the full /assistant page — drive the
 * same backend thread (/api/concierge/*) through this hook, so a conversation
 * started in one continues seamlessly in the other.
 *
 * Sending prefers the SSE endpoint (live "planning → checking listings →
 * self-checking" progress via `phase`); any streaming hiccup falls back to the
 * plain JSON endpoint, so streaming is purely progressive enhancement.
 */
export function useConciergeChat(active) {
  const [loaded, setLoaded] = useState(false);
  const [enabled, setEnabled] = useState(true);
  const [messages, setMessages] = useState([]);
  const [sending, setSending] = useState(false);
  const [phase, setPhase] = useState(null);
  const [hasMore, setHasMore] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const data = await apiFetch("/concierge/history/");
      setEnabled(data.enabled !== false);
      setMessages(data.messages || []);
      setHasMore(Boolean(data.has_more));
    } catch {
      // History is a nicety — start an empty conversation instead of failing.
      setMessages([]);
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (active && !loaded) loadHistory();
  }, [active, loaded, loadHistory]);

  const appendReply = useCallback((data) => {
    setMessages((prev) => [
      ...prev,
      {
        id: data.message_id ?? `reply-${Date.now()}`,
        role: "assistant",
        content: data.reply,
        used_tools: data.used_tools,
        meta: data.meta,
        rating: null,
        error: Boolean(data.degraded),
      },
    ]);
  }, []);

  const streamTurn = useCallback(
    async (text) => {
      const response = await fetch("/api/concierge/chat/stream/", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
        body: JSON.stringify({ message: text }),
      });
      if (!response.ok || !response.body) throw new Error(`stream unavailable (${response.status})`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let done = false;
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          if (!frame.startsWith("data: ")) continue;
          const event = JSON.parse(frame.slice(6));
          if (event.type === "phase" && mounted.current) {
            setPhase(event);
          } else if (event.type === "done") {
            done = true;
            if (mounted.current) appendReply(event);
          }
        }
      }
      if (!done) throw new Error("stream ended without a result");
    },
    [appendReply],
  );

  const send = useCallback(
    async (rawText) => {
      const text = rawText.trim();
      if (!text || sending) return;
      setSending(true);
      setPhase(null);
      setMessages((prev) => [...prev, { id: `local-${Date.now()}`, role: "user", content: text }]);
      try {
        try {
          await streamTurn(text);
        } catch {
          // Streaming is an enhancement; the JSON endpoint is the contract.
          // Agentic turns can take a while — longer leash than the 15s default.
          const data = await apiFetch("/concierge/chat/", {
            method: "POST",
            body: { message: text },
            timeout: 60_000,
          });
          appendReply(data);
        }
      } catch {
        setMessages((prev) => [
          ...prev,
          { id: `err-${Date.now()}`, role: "assistant", content: ERROR_BUBBLE, error: true },
        ]);
      } finally {
        if (mounted.current) {
          setSending(false);
          setPhase(null);
        }
      }
    },
    [sending, streamTurn, appendReply],
  );

  const sendFeedback = useCallback(async (messageId, rating) => {
    setMessages((prev) =>
      prev.map((message) =>
        message.id === messageId
          ? { ...message, rating: rating === "up" ? 1 : rating === "down" ? -1 : null }
          : message,
      ),
    );
    try {
      await apiFetch(`/concierge/messages/${messageId}/feedback/`, {
        method: "POST",
        body: { rating },
      });
    } catch {
      // Optimistic UI stands; feedback is best-effort telemetry.
    }
  }, []);

  const loadEarlier = useCallback(async () => {
    const oldest = messages.find((message) => typeof message.id === "number");
    if (!oldest) return;
    try {
      const data = await apiFetch(`/concierge/history/?before=${oldest.id}`);
      setMessages((prev) => [...(data.messages || []), ...prev]);
      setHasMore(Boolean(data.has_more));
    } catch {
      // Scrollback is a nicety; the button simply remains for a retry.
    }
  }, [messages]);

  const reset = useCallback(async () => {
    try {
      await apiFetch("/concierge/reset/", { method: "POST" });
    } catch {
      // A failed reset just leaves history in place; nothing to surface.
      return;
    }
    setMessages([]);
    setHasMore(false);
  }, []);

  return { loaded, enabled, messages, sending, phase, hasMore, send, sendFeedback, loadEarlier, reset };
}
