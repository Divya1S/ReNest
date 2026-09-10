import { ShieldCheck } from "lucide-react";
import React from "react";
import { NavLink } from "react-router-dom";

import PageSection from "../PageSection";

export const RISK_TABS = [
  { to: "/risk", label: "Overview", end: true },
  { to: "/risk/transactions", label: "Transactions" },
  { to: "/risk/lab", label: "Fraud Lab" },
  { to: "/risk/investigator", label: "Investigator" },
  { to: "/risk/agents", label: "Agent Sandbox" },
  { to: "/risk/health", label: "System Health" },
];

/**
 * Shared frame for every Risk Intelligence page: kicker, title, section tabs.
 * Tabs are real links, so they are keyboard reachable and announce as
 * navigation; the active one is marked with aria-current by NavLink.
 */
export default function RiskShell({ title, subtitle, actions, children }) {
  return (
    <div className="mx-auto max-w-[1200px] px-4 pb-20 md:px-8">
      <PageSection className="pt-8 pb-5">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="sticker mb-3 bg-[color:var(--color-night)] text-white">
              <ShieldCheck size={14} aria-hidden="true" />
              Risk Intelligence
            </div>
            <h1 className="text-[34px] font-black leading-tight tracking-[-0.025em] text-[color:var(--color-ink)] dark:text-white">
              {title}
            </h1>
            {subtitle ? <p className="mt-2 max-w-2xl text-[color:var(--text-muted)]">{subtitle}</p> : null}
          </div>
          {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
        </div>
        <nav aria-label="Risk Intelligence sections" className="mt-6 -mx-1 overflow-x-auto">
          <ul className="flex min-w-max gap-1 px-1">
            {RISK_TABS.map((tab) => (
              <li key={tab.to}>
                <NavLink
                  to={tab.to}
                  end={tab.end}
                  className={({ isActive }) =>
                    `inline-flex items-center rounded-full px-3.5 py-2 text-[0.8125rem] font-medium transition-colors ${
                      isActive
                        ? "bg-[color:var(--color-tag)] text-white"
                        : "text-[color:var(--text-muted)] hover:bg-[color:var(--bg-tag-soft)] hover:text-[color:var(--color-ink)]"
                    }`
                  }
                >
                  {tab.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </PageSection>
      {children}
    </div>
  );
}
