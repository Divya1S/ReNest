import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowRight,
  Camera,
  Check,
  MapPinned,
  Sparkles,
  Minus,
  Plus,
  Recycle,
  Search,
  Truck,
  ShieldCheck,
} from "lucide-react";
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import CountUpNumber from "../components/CountUpNumber";
import { usePageTitle } from "../hooks/usePageTitle";
import { apiFetch } from "../lib/api";

const featureCards = [
  {
    kicker: "Room-first workflow",
    title: "Start from photos, not repetitive forms.",
    body: "Upload room photos, tag rescue-worthy items with hotspots, and create structured drafts while the whole room is still visible.",
    icon: Camera,
    bg: "bg-[rgba(138,29,69,0.07)]",
    iconColor: "text-[color:var(--color-tag)]",
  },
  {
    kicker: "Fast triage",
    title: "One board for every move-out decision.",
    body: "The Clear-Out Board keeps every draft in one place so decisions happen faster and fewer useful items get lost in the rush.",
    icon: Truck,
    bg: "bg-[rgba(30,48,74,0.07)]",
    iconColor: "text-[color:var(--color-teal)]",
  },
  {
    kicker: "Local handoff",
    title: "Lower move-in costs for the next student.",
    body: "ReNest helps free and low-cost essentials stay on campus, while donation hubs catch items that shouldn't end up in a dumpster.",
    icon: Recycle,
    bg: "bg-amber-50 dark:bg-amber-900/10",
    iconColor: "text-amber-600 dark:text-amber-400",
  },
];

const faqs = [
  {
    question: "How does the room scan work?",
    answer: "Snap a few photos of your dorm room. Our studio lets you tag rescue-worthy items with simple hotspots — much faster than listing items one by one.",
  },
  {
    question: "Is there a fee for students?",
    answer: "ReNest is free for students to browse and reserve items. Listing items for sale may have small campus-specific transaction limits to keep costs low.",
  },
  {
    question: "What happens if an item isn't claimed?",
    answer: "If your item hasn't been reserved by your move-out deadline, our 'Route to Hub' feature gives you instructions for the nearest campus donation drop-off.",
  },
];

function FaqItem({ question, answer, id }) {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <div className="border-b border-[rgba(0,0,0,0.08)] dark:border-[rgba(255,255,255,0.08)] last:border-0">
      <button
        type="button"
        aria-expanded={isOpen}
        aria-controls={`faq-${id}`}
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full items-center justify-between py-5 text-left group"
      >
        <span className="text-[1rem] font-semibold text-[color:var(--color-ink)] group-hover:text-[color:var(--color-tag)] transition-colors">
          {question}
        </span>
        <div className={`shrink-0 ml-4 transition-transform duration-300 ${isOpen ? "rotate-45" : ""}`}>
          <Plus size={18} className="text-[color:var(--text-muted)]" />
        </div>
      </button>
      <AnimatePresence>
        {isOpen && (
          <motion.div
            id={`faq-${id}`}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <p className="pb-5 text-[color:var(--text-muted)] leading-relaxed text-sm">
              {answer}
            </p>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default function LandingPage() {
  usePageTitle("Home");
  const [impact, setImpact] = useState(null);

  useEffect(() => {
    apiFetch("/impact").then(setImpact).catch(() => {});
  }, []);

  return (
    <div className="pb-20 overflow-x-hidden">

      {/* ── Hero ─────────────────────────────────────────────────────────── */}
      <section className="px-4 md:px-8 max-w-[1200px] mx-auto flex flex-col items-center text-center mt-16 md:mt-20">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="flex flex-col items-center"
        >
          <div className="inline-flex items-center gap-2 rounded-full bg-[rgba(138,29,69,0.08)] px-4 py-1.5 text-[0.7rem] font-semibold uppercase tracking-widest text-[color:var(--color-tag)] mb-6">
            <Sparkles size={12} />
            Campus move-out rescue
          </div>

          <h1 className="text-[2.6rem] sm:text-[3.4rem] md:text-[3.8rem] font-bold leading-[1.1] tracking-[-0.025em] max-w-3xl mb-5 text-[color:var(--color-ink)]">
            Keep usable dorm essentials out of the{" "}
            <span className="text-[color:var(--color-tag)]">dumpster</span>.
          </h1>

          <p className="text-[1.0625rem] md:text-[1.125rem] text-[color:var(--text-muted)] max-w-2xl mb-8 leading-relaxed">
            The sustainable campus marketplace designed for students. Buy, sell, and rescue premium dorm gear during move-out season.
          </p>

          <div className="flex flex-col sm:flex-row gap-3 w-full sm:w-auto mb-12">
            <Link
              to="/register"
              className="inline-flex items-center justify-center gap-2 rounded-full bg-[color:var(--color-tag)] text-white px-8 py-3.5 text-[0.9375rem] font-semibold transition-opacity hover:opacity-85 active:opacity-70"
            >
              Get started free
              <ArrowRight size={17} />
            </Link>
            <Link
              to="/browse"
              className="inline-flex items-center justify-center gap-2 rounded-full border-2 border-[color:var(--color-tag)] text-[color:var(--color-tag)] px-8 py-3.5 text-[0.9375rem] font-semibold transition-colors hover:bg-[rgba(138,29,69,0.05)]"
            >
              <Search size={17} />
              Browse listings
            </Link>
          </div>
        </motion.div>

        {/* Hero visual */}
        <motion.div
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.15 }}
          className="w-full max-w-4xl"
        >
          {/* Deliberately light in both themes — framed like a product still */}
          <div className="relative w-full aspect-[16/9] bg-white rounded-[20px] border border-black/10 dark:border-white/10 shadow-sm overflow-hidden">
            <img
              src="/images/hero-room-vignette.png"
              alt="Illustration of a dorm room with a lamp, storage bin, and mini-fridge tagged for rescue"
              className="absolute inset-0 h-full w-full object-cover"
              fetchPriority="high"
            />

            {/* Price chips anchored to the illustration's gold tag targets */}
            {[
              { label: "Free", left: "47.2%", top: "42.3%", delay: 0.55 },
              { label: "$12", left: "70.2%", top: "68.8%", delay: 0.7 },
              { label: "$45", left: "90.9%", top: "51.8%", delay: 0.85 },
            ].map((chip) => (
              <span
                key={chip.label}
                className="absolute"
                style={{ left: chip.left, top: chip.top, transform: "translate(-50%, -135%)" }}
              >
                <motion.span
                  initial={{ opacity: 0, scale: 0.7 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: chip.delay, duration: 0.3 }}
                  className="block rounded-full bg-[color:var(--accent-tag-deep,#6a002f)] px-2.5 py-1 text-[11px] font-semibold text-white shadow-md"
                >
                  {chip.label}
                </motion.span>
              </span>
            ))}
            {/* Verified badge */}
            <div className="absolute bottom-4 left-4 flex items-center gap-2 bg-white px-3 py-2 rounded-xl border border-black/10 shadow-sm">
              <ShieldCheck size={15} className="text-[color:var(--color-tag)]" />
              <span className="text-[0.7rem] font-bold text-[color:var(--color-tag)] uppercase tracking-wider">Verified Sustainability Impact</span>
            </div>
          </div>
        </motion.div>
      </section>

      {/* ── Live Impact (real numbers from /api/impact) ───────────────────── */}
      {impact && (
        <section className="mt-20 px-4 md:px-8 max-w-[1200px] mx-auto">
          <div className="rounded-[2rem] border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)] px-8 py-10">
            <div className="text-center mb-8">
              <p className="text-[0.7rem] font-semibold uppercase tracking-[0.2em] text-[color:var(--text-muted)]">Live campus impact</p>
              <h2 className="mt-2 text-[1.6rem] font-bold tracking-[-0.02em] text-[color:var(--color-ink)] dark:text-white">
                Real stuff rescued. Real waste avoided.
              </h2>
              <p className="mt-2 text-sm text-[color:var(--text-muted)]">
                Counted live from actual rescues on this platform — no vanity numbers.
              </p>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-6 text-center">
              <div>
                <p className="text-4xl font-bold text-[color:var(--color-tag)]">
                  <CountUpNumber value={impact.total_rescued} />
                </p>
                <p className="mt-1 text-sm text-[color:var(--text-muted)]">Items rescued</p>
              </div>
              <div>
                <p className="text-4xl font-bold text-[color:var(--color-tag)]">
                  ${impact.total_retail_value > 999
                    ? `${(impact.total_retail_value / 1000).toFixed(1)}k`
                    : Number(impact.total_retail_value).toFixed(0)}
                </p>
                <p className="mt-1 text-sm text-[color:var(--text-muted)]">Retail value saved</p>
              </div>
              <div>
                <p className="text-4xl font-bold text-[color:var(--color-tag)]">
                  <CountUpNumber value={Math.round(impact.estimated_lbs_diverted)} />
                  <span className="text-2xl font-semibold"> lbs</span>
                </p>
                <p className="mt-1 text-sm text-[color:var(--text-muted)]">Diverted from landfill</p>
              </div>
              <div>
                <p className="text-4xl font-bold text-[color:var(--color-tag)]">
                  <CountUpNumber value={impact.rescue_rate_pct} />
                  <span className="text-2xl font-semibold">%</span>
                </p>
                <p className="mt-1 text-sm text-[color:var(--text-muted)]">Rescue rate</p>
              </div>
            </div>
          </div>
        </section>
      )}

      {/* ── Features ─────────────────────────────────────────────────────── */}
      <section className="mt-24 px-4 md:px-8 max-w-[1200px] mx-auto">
        <div className="text-center mb-12">
          <p className="text-[0.7rem] font-semibold uppercase tracking-[0.2em] text-[color:var(--text-muted)]">How it works</p>
          <h2 className="mt-3 text-[2rem] sm:text-[2.6rem] font-bold tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white">
            A smarter way to move out.
          </h2>
          <p className="mt-3 text-[color:var(--text-muted)] max-w-xl mx-auto leading-relaxed">
            Three steps to a faster, cleaner, cheaper move-out for you and the next student after you.
          </p>
        </div>

        <div className="grid gap-5 md:grid-cols-3">
          {featureCards.map((card, i) => {
            const Icon = card.icon;
            return (
              <motion.div
                key={card.kicker}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.1, duration: 0.4 }}
                whileHover={{ y: -4 }}
                className="paper-panel p-8"
              >
                <div className={`inline-flex h-12 w-12 items-center justify-center rounded-[0.875rem] ${card.bg} ${card.iconColor}`}>
                  <Icon size={22} />
                </div>
                <p className="mt-6 text-[0.7rem] font-semibold uppercase tracking-widest text-[color:var(--text-muted)]">{card.kicker}</p>
                <h3 className="mt-2 text-[1.2rem] font-bold tracking-[-0.01em] text-[color:var(--color-ink)] dark:text-white leading-snug">{card.title}</h3>
                <p className="mt-3 text-sm leading-relaxed text-[color:var(--text-muted)]">{card.body}</p>
              </motion.div>
            );
          })}
        </div>
      </section>

      {/* ── Comparison Table ─────────────────────────────────────────────── */}
      <section className="mt-24 px-4 md:px-8 max-w-[900px] mx-auto">
        <div className="text-center mb-10">
          <p className="text-[0.7rem] font-semibold uppercase tracking-[0.2em] text-[color:var(--text-muted)]">The ReNest difference</p>
          <h2 className="mt-3 text-[2rem] sm:text-[2.6rem] font-bold tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white">
            Better for you. Better for the planet.
          </h2>
        </div>

        <div className="paper-panel overflow-hidden">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-black/8 dark:border-white/8">
                <th className="px-6 py-4 text-sm font-semibold text-[color:var(--text-muted)]">Feature</th>
                <th className="px-6 py-4 text-sm font-semibold text-[color:var(--color-tag)]">ReNest</th>
                <th className="px-6 py-4 text-sm font-semibold text-[color:var(--text-muted)]">Marketplace Apps</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-black/6 dark:divide-white/6">
              {[
                { f: "Room Scan Triage", d: true, m: false },
                { f: "Campus-Scoped Listings", d: true, m: false },
                { f: "One-Click Hub Donation", d: true, m: false },
                { f: "Move-Out Deadline Tracking", d: true, m: false },
                { f: "Local Pickup Coordination", d: true, m: true },
              ].map((row, i) => (
                <tr key={i} className="hover:bg-[rgba(0,0,0,0.02)] dark:hover:bg-white/3 transition-colors">
                  <td className="px-6 py-4 text-sm font-medium text-[color:var(--color-ink)] dark:text-white">{row.f}</td>
                  <td className="px-6 py-4">
                    <Check size={17} className="text-[color:var(--color-tag)]" aria-hidden="true" />
                    <span className="sr-only">Included</span>
                  </td>
                  <td className="px-6 py-4">
                    {row.m
                      ? <Check size={17} style={{ color: "rgba(86,65,70,0.3)" }} aria-hidden="true" />
                      : <Minus size={17} style={{ color: "rgba(86,65,70,0.2)" }} aria-hidden="true" />
                    }
                    <span className="sr-only">{row.m ? "Included" : "Not included"}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── FAQ ──────────────────────────────────────────────────────────── */}
      <section className="mt-24 px-4 md:px-8 max-w-[720px] mx-auto">
        <div className="text-center mb-8">
          <p className="text-[0.7rem] font-semibold uppercase tracking-[0.2em] text-[color:var(--text-muted)]">FAQ</p>
          <h2 className="mt-3 text-[2rem] font-bold tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white">Common questions.</h2>
        </div>
        <div className="paper-panel px-6 sm:px-8 py-2">
          {faqs.map((faq, i) => (
            <FaqItem key={faq.question} id={i} {...faq} />
          ))}
        </div>
      </section>

      {/* ── CTA ──────────────────────────────────────────────────────────── */}
      <section className="mt-16 px-4 md:px-8 max-w-[1200px] mx-auto">
        <div
          className="relative rounded-[20px] overflow-hidden px-8 py-20 text-center"
          style={{ background: "#1e304a" }}
        >
          <div className="absolute inset-0 pointer-events-none" style={{ background: "radial-gradient(circle at 70% 20%, rgba(217,164,65,0.18), transparent 50%)" }} />
          <div className="relative z-10 max-w-xl mx-auto">
            <p className="text-[0.7rem] font-semibold uppercase tracking-[0.2em] mb-4" style={{ color: "rgba(255,255,255,0.55)" }}>
              Get started today
            </p>
            <h2 className="text-[2.2rem] sm:text-[2.8rem] font-bold leading-tight tracking-[-0.025em] text-white mb-4">
              Ready to rescue your room?
            </h2>
            <p className="text-[1rem] leading-relaxed mb-8" style={{ color: "rgba(255,255,255,0.7)" }}>
              Make your campus move-out faster, cheaper, and more sustainable — starting with your own room.
            </p>
            <div className="flex flex-col sm:flex-row justify-center gap-3">
              <Link
                to="/register"
                className="inline-flex items-center justify-center gap-2 rounded-full bg-white px-8 py-3 text-[0.9375rem] font-semibold transition-opacity hover:opacity-90"
                style={{ color: "#1e304a" }}
              >
                Get Started Free
                <ArrowRight size={16} />
              </Link>
              <Link
                to="/hubs"
                className="inline-flex items-center justify-center gap-2 rounded-full px-8 py-3 text-[0.9375rem] font-semibold text-white transition-colors hover:bg-white/10"
                style={{ border: "1px solid rgba(255,255,255,0.22)" }}
              >
                <MapPinned size={16} />
                Find My Campus Hub
              </Link>
            </div>
          </div>
        </div>
      </section>

    </div>
  );
}
