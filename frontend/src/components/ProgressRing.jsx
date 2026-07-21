import { motion } from "framer-motion";
import React from "react";

export default function ProgressRing({ radius = 40, stroke = 4, progress = 0, color = "var(--color-tag)" }) {
  const normalizedRadius = radius - stroke * 2;
  const circumference = normalizedRadius * 2 * Math.PI;
  const strokeDashoffset = circumference - (progress / 100) * circumference;

  return (
    <svg
      role="img"
      aria-label={`Progress: ${progress}%`}
      height={radius * 2}
      width={radius * 2}
      className="rotate-[-90deg]"
    >
      <circle
        stroke="currentColor"
        fill="transparent"
        strokeWidth={stroke}
        strokeDasharray={circumference + " " + circumference}
        style={{ strokeDashoffset: circumference }}
        className="text-slate-200 dark:text-slate-700"
        r={normalizedRadius}
        cx={radius}
        cy={radius}
      />
      <motion.circle
        stroke={color}
        fill="transparent"
        strokeWidth={stroke}
        strokeDasharray={circumference + " " + circumference}
        initial={{ strokeDashoffset: circumference }}
        animate={{ strokeDashoffset }}
        transition={{ duration: 1, ease: "easeOut" }}
        strokeLinecap="round"
        r={normalizedRadius}
        cx={radius}
        cy={radius}
      />
    </svg>
  );
}
