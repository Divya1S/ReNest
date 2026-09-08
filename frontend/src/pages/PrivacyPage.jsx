import { FileText, Shield } from "lucide-react";
import React from "react";
import { Link } from "react-router-dom";

import { usePageTitle } from "../hooks/usePageTitle";

const UPDATED = "July 2026";

function Section({ title, children }) {
  return (
    <section className="mt-8">
      <h2 className="text-[20px] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white">
        {title}
      </h2>
      <div className="mt-3 space-y-3 text-[15px] leading-relaxed text-[color:var(--text-muted)]">
        {children}
      </div>
    </section>
  );
}

export default function PrivacyPage() {
  usePageTitle("Privacy & Terms");

  return (
    <div className="mx-auto max-w-3xl px-4 md:px-8 pb-20">
      <section className="pt-8 pb-4">
        <div className="flex items-center gap-2 text-[color:var(--color-tag)]">
          <Shield size={16} />
          <span className="text-[12px] font-semibold uppercase tracking-[0.1em]">Legal</span>
        </div>
        <h1 className="mt-3 text-[34px] font-bold tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white">
          Privacy Policy
        </h1>
        <p className="mt-2 text-[14px] text-[color:var(--text-muted)]">Last updated: {UPDATED}</p>
      </section>

      <div className="bg-[color:var(--color-surface)] rounded-[20px] border border-black/10 dark:border-white/10 shadow-[0px_6px_20px_rgba(0,0,0,0.04)] px-6 py-2 sm:px-8">
        <Section title="What we collect">
          <p>
            When you create an account, ReNest stores your display name, email address, and campus.
            When you use the product, we store the content you create: listings and their photos,
            room scans, reservations, pickup messages, feedback, and reports.
          </p>
          <p>
            We record basic product events (for example, that a listing was viewed) to power features
            like trending items and personalised ordering. We do not buy data about you from anyone,
            and we do not sell or rent your data to anyone.
          </p>
        </Section>

        <Section title="How it's used">
          <p>
            Your name and campus are shown to other students alongside your listings and requests —
            that&apos;s the product. Your email is used for account security (verification, password
            reset) and for the notifications you can control in{" "}
            <Link to="/settings/notifications" className="font-semibold text-[color:var(--color-tag)] hover:opacity-70">
              notification preferences
            </Link>
            , including a one-click unsubscribe in every email.
          </p>
          <p>
            Pickup coordination messages are visible only to the two people in that handoff, plus
            moderators if either party files a report.
          </p>
        </Section>

        <Section title="Cookies & sessions">
          <p>
            ReNest uses a session cookie to keep you signed in and a CSRF cookie to protect forms.
            There are no third-party advertising or tracking cookies. Anonymous browsing uses a
            session key only to avoid double-counting listing views.
          </p>
        </Section>

        <Section title="AI features">
          <p>
            Room-scan photos you submit to AI detection, listing text you generate with AI
            assistance, and the messages you send the Nest Concierge are processed by Google&apos;s
            Gemini API to produce suggestions. Content is sent for that request only.
          </p>
          <p>
            The concierge can read your own marketplace data (your listings, reservations and
            move-out progress) to answer your questions. It cannot change anything on your behalf,
            and it never sees another student&apos;s private data. Your conversation is stored so the
            assistant can follow the thread; clearing it from the concierge panel deletes it.
          </p>
          <p>
            Every AI feature is optional. When the server is not configured with an AI key, those
            surfaces switch off and the rest of ReNest works normally.
          </p>
        </Section>

        <Section title="Your data, your control">
          <p>
            You can download everything we hold about you, or delete your account, from{" "}
            <Link to="/settings" className="font-semibold text-[color:var(--color-tag)] hover:opacity-70">
              settings
            </Link>
            . Deleting a listing removes it from the marketplace immediately.
          </p>
          <p>
            Deleting your account cancels your open reservations, removes your listings, saved
            items, saved searches, notifications, push subscriptions and concierge history, and
            anonymises your profile. Messages and feedback you exchanged with another student stay
            attached to that person&apos;s own handoff record, shown as &ldquo;Deleted User&rdquo;, because they are
            part of their history too.
          </p>
        </Section>
      </div>

      <section className="pt-12 pb-4" id="terms">
        <div className="flex items-center gap-2 text-[color:var(--color-tag)]">
          <FileText size={16} />
          <span className="text-[12px] font-semibold uppercase tracking-[0.1em]">Terms</span>
        </div>
        <h1 className="mt-3 text-[34px] font-bold tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white">
          Terms of Use
        </h1>
      </section>

      <div className="bg-[color:var(--color-surface)] rounded-[20px] border border-black/10 dark:border-white/10 shadow-[0px_6px_20px_rgba(0,0,0,0.04)] px-6 py-2 sm:px-8">
        <Section title="The short version">
          <p>
            ReNest is a coordination tool for students exchanging dorm items on their own campus.
            Items are exchanged between students directly — ReNest does not take payment, hold
            funds, inspect items, or act as a party to any exchange.
          </p>
        </Section>

        <Section title="House rules">
          <p>
            List only items you own and can hand over. No prohibited items (weapons, alcohol,
            prescription goods, anything your campus bans). Show up for pickups you commit to, or
            cancel in the app so the item can go to someone else. Repeated no-shows and misleading
            listings lower your completion rate and can hide your listings from browse.
          </p>
        </Section>

        <Section title="Safety">
          <p>
            Meet in public campus locations — lobbies, front desks, and donation hubs. Use the
            in-app PIN to confirm handoffs. If something feels off, walk away and use the report
            button; a moderator reviews every report.
          </p>
        </Section>

        <Section title="Liability">
          <p>
            Items are offered as-is by their owners. ReNest provides the platform without warranty
            of any kind and is not liable for the condition, safety, or delivery of exchanged
            items, to the maximum extent permitted by law.
          </p>
        </Section>
      </div>
    </div>
  );
}
