import { motion } from "framer-motion";
import React from "react";

export default function ImpactCard({ label, value, tone = "box", icon: Icon, detail }) {
  const tones = {
    box: "bg-[linear-gradient(145deg,rgba(255,247,233,0.98)_0%,rgba(247,234,208,0.98)_100%)] text-[color:var(--color-night)] border-[rgba(217,164,65,0.26)] dark:bg-[linear-gradient(145deg,rgba(226,180,93,0.14)_0%,rgba(217,164,65,0.08)_100%)] dark:border-[rgba(226,180,93,0.22)] dark:text-[color:var(--color-box)]",
    blue: "bg-[linear-gradient(145deg,rgba(243,235,240,0.98)_0%,rgba(220,230,243,0.96)_100%)] text-[color:var(--color-night)] border-[rgba(138,29,69,0.14)] dark:bg-[linear-gradient(145deg,rgba(225,122,155,0.12)_0%,rgba(154,182,213,0.08)_100%)] dark:border-[rgba(225,122,155,0.14)] dark:text-slate-200",
    teal: "bg-[linear-gradient(145deg,var(--color-tag)_0%,var(--color-teal)_100%)] text-white border-[rgba(30,48,74,0.24)]",
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      whileHover={{ y: -4 }}
      aria-label={`${label}: ${value}`}
      className={`rounded-[1.9rem] border p-5 shadow-[0_20px_44px_rgba(20,17,24,0.08)] ${tones[tone]}`}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[0.68rem] font-semibold uppercase tracking-[0.22em] opacity-75">{label}</p>
          <p className="mt-3 text-4xl font-bold">{value}</p>
        </div>
        {Icon ? (
          <div className="rounded-[1.1rem] bg-white/18 p-3 backdrop-blur-sm shadow-[inset_0_1px_0_rgba(255,255,255,0.28)]">
            <Icon size={20} className="opacity-90" aria-hidden="true" />
          </div>
        ) : null}
      </div>
      {detail ? <p className="mt-4 text-sm leading-6 opacity-80">{detail}</p> : null}
    </motion.div>
  );
}
