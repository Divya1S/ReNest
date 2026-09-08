import { RotateCcw, Sparkles } from "lucide-react";
import React from "react";

import ConciergeConversation from "../components/concierge/ConciergeConversation";
import { useConciergeChat } from "../components/concierge/useConciergeChat";

/**
 * Full-page surface for the Nest Concierge. Same thread, same backend, same
 * conversation core as the floating widget — this page just gives long
 * conversations room to breathe.
 */
export default function AssistantPage() {
  const chat = useConciergeChat(true);

  return (
    <div className="mx-auto flex h-[calc(100dvh-16rem)] min-h-[26rem] max-w-3xl flex-col">
      <header className="flex items-end justify-between gap-4 pb-5">
        <div>
          <p className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.14em] text-[color:var(--color-box)]">
            <Sparkles size={13} aria-hidden="true" />
            Nest Concierge
          </p>
          <h1 className="mt-1.5 text-2xl font-bold tracking-tight text-[color:var(--color-night)]">
            Your move-out assistant
          </h1>
          <p className="mt-1 text-sm text-[color:var(--text-muted)]">
            Searches live listings, checks your reservations and scan progress, and answers
            how-ReNest-works questions. It looks things up — reserving and posting stay with you.
          </p>
        </div>
        {chat.messages.length > 0 && (
          <button
            type="button"
            onClick={chat.reset}
            className="secondary-button shrink-0"
          >
            <RotateCcw size={15} aria-hidden="true" />
            Clear chat
          </button>
        )}
      </header>

      <section
        aria-label="Concierge conversation"
        className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-[color:var(--color-line-strong)] bg-[color:var(--bg-surface)] shadow-sm"
      >
        <ConciergeConversation chat={chat} autoFocus />
      </section>
    </div>
  );
}
