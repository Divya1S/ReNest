import { ArrowUpRight, Send, ThumbsDown, ThumbsUp, Zap } from "lucide-react";
import React, { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { GenerativeBlocks, extractSuggestions } from "./generativeUi";

/**
 * The conversation surface shared by the floating widget and the /assistant
 * page: message list, generative-UI blocks, follow-up chips, and composer.
 * All state lives in useConciergeChat — this component only renders it.
 */

const TOOL_LABELS = {
  search_listings: "live listings",
  get_my_activity: "your activity",
  get_move_out_progress: "your move-out",
  search_help: "help guide",
};

const PHASE_LABELS = {
  routing: "Reading your message\u2026",
  thinking: "Thinking\u2026",
  planning: "Planning an approach\u2026",
  plan_ready: "Plan ready \u2014 working through it\u2026",
  reflecting: "Double-checking the answer\u2026",
  revising: "Fixing an issue it found\u2026",
};

function phaseLabel(phase) {
  if (!phase) return null;
  if (phase.phase === "tool") {
    const label = TOOL_LABELS[phase.tool];
    return label ? `Checking ${label}\u2026` : "Looking things up\u2026";
  }
  return PHASE_LABELS[phase.phase] || null;
}

const SUGGESTIONS = [
  "What's available right now?",
  "How do handoffs work?",
  "How's my move-out going?",
];

function bubbleCaption(message) {
  const meta = message.meta || {};
  if (meta.cached) {
    return { icon: true, text: "Instant answer" };
  }
  const parts = [];
  const tools = (message.used_tools || []).map((name) => TOOL_LABELS[name]).filter(Boolean);
  if (tools.length > 0) parts.push(`Checked ${[...new Set(tools)].join(", ")}`);
  if (meta.plan?.steps?.length) {
    const steps = meta.plan.steps.length;
    parts.push(`planned ${steps} step${steps === 1 ? "" : "s"}`);
    if (meta.reflection) parts.push(meta.revised ? "self-corrected" : "self-checked");
  }
  return parts.length ? { icon: false, text: parts.join(" · ") } : null;
}

function FeedbackButtons({ message, onFeedback }) {
  if (typeof message.id !== "number") return null; // only persisted replies are ratable
  const items = [
    { key: "up", label: "Helpful", Icon: ThumbsUp, active: message.rating === 1 },
    { key: "down", label: "Not helpful", Icon: ThumbsDown, active: message.rating === -1 },
  ];
  return (
    <span className="ml-2 inline-flex items-center gap-0.5 align-middle">
      {items.map(({ key, label, Icon, active }) => (
        <button
          key={key}
          type="button"
          aria-label={label}
          aria-pressed={active}
          onClick={() => onFeedback(message.id, active ? "clear" : key)}
          className={
            active
              ? "rounded p-1 text-[color:var(--color-box)]"
              : "rounded p-1 text-[color:var(--text-muted)] opacity-60 hover:opacity-100"
          }
        >
          <Icon size={12} aria-hidden="true" />
        </button>
      ))}
    </span>
  );
}

function Bubble({ message, onFeedback }) {
  const isUser = message.role === "user";
  const caption = isUser || message.error ? null : bubbleCaption(message);
  const blocks = (message.meta?.ui_blocks || []).filter((block) => block.type !== "suggestions");
  return (
    <div className={isUser ? "flex justify-end" : "flex justify-start"}>
      <div className="max-w-[85%]">
        <div
          className={
            isUser
              ? "rounded-2xl rounded-br-sm bg-[color:var(--color-box)] px-3.5 py-2 text-sm leading-relaxed text-white"
              : message.error
                ? "rounded-2xl rounded-bl-sm border border-amber-300/60 bg-amber-50 px-3.5 py-2 text-sm leading-relaxed text-amber-900 dark:border-amber-500/40 dark:bg-amber-900/20 dark:text-amber-200"
                : "rounded-2xl rounded-bl-sm bg-[color:var(--bg-surface-2)] px-3.5 py-2 text-sm leading-relaxed text-[color:var(--color-night)]"
          }
        >
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
        </div>
        <GenerativeBlocks blocks={blocks} />
        {!isUser && !message.error && (
          <p className="mt-1 flex items-center gap-1 px-1 text-[11px] text-[color:var(--text-muted)]">
            {caption?.icon && <Zap size={11} aria-hidden="true" />}
            {caption?.text}
            <FeedbackButtons message={message} onFeedback={onFeedback} />
          </p>
        )}
      </div>
    </div>
  );
}

function TypingDots() {
  return (
    <div className="flex justify-start" aria-hidden="true">
      <div className="flex items-center gap-1 rounded-2xl rounded-bl-sm bg-[color:var(--bg-surface-2)] px-4 py-3">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-1.5 w-1.5 animate-bounce rounded-full bg-[color:var(--text-muted)] motion-reduce:animate-none"
            style={{ animationDelay: `${i * 140}ms` }}
          />
        ))}
      </div>
    </div>
  );
}

export default function ConciergeConversation({ chat, autoFocus = false }) {
  const { loaded, enabled, messages, sending, phase, hasMore, send, sendFeedback, loadEarlier } = chat;
  const [input, setInput] = useState("");
  const inputRef = useRef(null);
  const scrollRef = useRef(null);
  const prependingRef = useRef(false);
  const inputId = useId();

  useEffect(() => {
    if (autoFocus) inputRef.current?.focus();
  }, [autoFocus]);

  useEffect(() => {
    if (prependingRef.current) return; // loading scrollback must not yank to bottom
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  const handleLoadEarlier = async () => {
    const el = scrollRef.current;
    const previousHeight = el?.scrollHeight ?? 0;
    prependingRef.current = true;
    await loadEarlier();
    requestAnimationFrame(() => {
      if (el) el.scrollTop = el.scrollHeight - previousHeight; // keep reading position
      prependingRef.current = false;
    });
  };

  const submit = (text) => {
    setInput("");
    send(text).finally(() => inputRef.current?.focus());
  };

  const showIntro = loaded && messages.length === 0;
  const lastMessage = messages[messages.length - 1];
  const followups =
    !sending && lastMessage?.role === "assistant" && !lastMessage.error
      ? extractSuggestions(lastMessage.meta?.ui_blocks)
      : [];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto px-4 py-4" aria-live="polite">
        {loaded && hasMore && (
          <div className="flex justify-center">
            <button
              type="button"
              onClick={handleLoadEarlier}
              className="rounded-full border border-[color:var(--color-line-strong)] px-3 py-1 text-xs font-medium text-[color:var(--text-muted)] hover:text-[color:var(--color-night)]"
            >
              Show earlier messages
            </button>
          </div>
        )}
        {!loaded && (
          <p className="text-sm text-[color:var(--text-muted)]">Opening your conversation…</p>
        )}
        {!enabled && loaded && (
          <p className="rounded-xl border border-[color:var(--color-line-strong)] bg-[color:var(--bg-surface-2)] px-3.5 py-2 text-sm text-[color:var(--text-muted)]">
            The concierge is offline right now. Everything else in ReNest works normally.
          </p>
        )}
        {showIntro && enabled && (
          <div>
            <p className="text-sm leading-relaxed text-[color:var(--color-night)]">
              Hi! I can search live listings, check your reservations and move-out progress,
              and explain how ReNest works. What do you need?
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => submit(suggestion)}
                  className="rounded-full border border-[color:var(--color-line-strong)] px-3 py-1.5 text-xs font-medium text-[color:var(--color-night)] hover:bg-[color:var(--bg-surface-2)]"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((message) => (
          <Bubble key={message.id} message={message} onFeedback={sendFeedback} />
        ))}
        {sending && (
          <div>
            <TypingDots />
            {phaseLabel(phase) && (
              <p className="mt-1.5 px-1 text-[11px] text-[color:var(--text-muted)]">
                {phaseLabel(phase)}
              </p>
            )}
          </div>
        )}
        {followups.length > 0 && (
          <div className="flex flex-wrap gap-2 pt-1">
            {followups.map(({ label, path }) =>
              path ? (
                <Link
                  key={label}
                  to={path}
                  className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-line-strong)] px-3 py-1.5 text-xs font-medium text-[color:var(--color-night)] hover:bg-[color:var(--bg-surface-2)]"
                >
                  {label}
                  <ArrowUpRight size={11} aria-hidden="true" />
                </Link>
              ) : (
                <button
                  key={label}
                  type="button"
                  onClick={() => submit(label)}
                  className="rounded-full border border-[color:var(--color-line-strong)] px-3 py-1.5 text-xs font-medium text-[color:var(--color-night)] hover:bg-[color:var(--bg-surface-2)]"
                >
                  {label}
                </button>
              ),
            )}
          </div>
        )}
      </div>

      <form
        className="flex items-end gap-2 border-t border-[color:var(--color-line-strong)] px-3 py-3"
        onSubmit={(event) => {
          event.preventDefault();
          submit(input);
        }}
      >
        <label htmlFor={inputId} className="sr-only">
          Message the concierge
        </label>
        <input
          id={inputId}
          ref={inputRef}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          disabled={!enabled || sending}
          maxLength={2000}
          placeholder={enabled ? "Ask about items, pickups, move-out…" : "Concierge unavailable"}
          autoComplete="off"
          className="min-w-0 flex-1 rounded-xl border border-[color:var(--color-line-strong)] bg-transparent px-3.5 py-2 text-sm text-[color:var(--color-night)] placeholder:text-[color:var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[color:var(--color-box)]"
        />
        <button
          type="submit"
          disabled={!enabled || sending || !input.trim()}
          aria-label="Send message"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[color:var(--color-box)] text-white disabled:opacity-40"
        >
          <Send size={16} aria-hidden="true" />
        </button>
      </form>
    </div>
  );
}
