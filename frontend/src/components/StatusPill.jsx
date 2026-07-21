import React from "react";

import { cn } from "../lib/cn";

const labels = {
  free: "Free",
  low_cost: "Low Cost",
  available: "Available",
  reserved: "Reserved",
  picked_up: "Picked Up",
  expired: "Expired",
  donated: "Donated",
  urgent: "Urgent",
  ready: "Ready to Publish",
  needs_info: "Needs Info",
  donation_route: "Donation Route",
  published: "Published",
};

const tones = {
  free: "border-[color:var(--color-line-strong)] bg-[color:var(--color-surface)] text-[color:var(--color-night)]",
  low_cost: "border-[rgba(217,164,65,0.3)] bg-[rgba(217,164,65,0.14)] text-[color:var(--color-night)] dark:bg-[rgba(226,180,93,0.18)] dark:text-[color:var(--color-box)]",
  available: "border-[color:var(--color-tag-soft)] bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)] dark:border-[rgba(225,122,155,0.2)] dark:bg-[rgba(225,122,155,0.12)] dark:text-[color:var(--color-tag)]",
  reserved: "border-[rgba(217,164,65,0.3)] bg-[rgba(217,164,65,0.14)] text-[color:var(--color-night)] dark:bg-[rgba(226,180,93,0.18)] dark:text-[color:var(--color-box)]",
  picked_up: "border-[rgba(30,48,74,0.22)] bg-[color:var(--color-night)] text-white dark:bg-[color:var(--color-surface-2)] dark:border-white/15",
  expired: "border-black/10 bg-[color:var(--color-surface-2)] text-[color:var(--text-muted)] dark:border-white/10",
  donated: "border-[color:var(--color-teal-soft)] bg-[color:var(--color-teal)] text-white dark:bg-[rgba(154,182,213,0.22)] dark:text-[color:var(--color-teal)] dark:border-[rgba(154,182,213,0.3)]",
  urgent: "border-[rgba(201,100,68,0.22)] bg-[rgba(201,100,68,0.12)] text-[color:var(--color-urgent)] dark:bg-[rgba(255,143,106,0.12)] dark:border-[rgba(255,143,106,0.22)]",
  ready: "border-[color:var(--color-tag-soft)] bg-[color:var(--color-tag-soft)] text-[color:var(--color-tag)] dark:border-[rgba(225,122,155,0.2)] dark:bg-[rgba(225,122,155,0.12)] dark:text-[color:var(--color-tag)]",
  needs_info: "border-[rgba(201,100,68,0.22)] bg-[rgba(201,100,68,0.12)] text-[color:var(--color-urgent)] dark:bg-[rgba(255,143,106,0.12)] dark:border-[rgba(255,143,106,0.22)]",
  donation_route: "border-[color:var(--color-teal-soft)] bg-[color:var(--color-teal-soft)] text-[color:var(--color-teal)] dark:bg-[rgba(154,182,213,0.12)] dark:border-[rgba(154,182,213,0.2)]",
  published: "border-[rgba(30,48,74,0.22)] bg-[color:var(--color-night)] text-white dark:bg-[color:var(--color-surface-2)] dark:border-white/15",
};

export default function StatusPill({ status, className }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-3 py-1.5 text-[0.64rem] font-semibold uppercase tracking-[0.16em] shadow-sm",
        tones[status] || "border-[color:var(--color-line)] bg-[color:var(--color-surface)] text-[color:var(--color-ink)]",
        className,
      )}
    >
      {labels[status] || status}
    </span>
  );
}
