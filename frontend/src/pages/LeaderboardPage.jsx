import { motion } from "framer-motion";
import { Building2, Medal, RefreshCw, Trophy } from "lucide-react";
import React from "react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { useApi } from "../hooks/useApi";
import { usePageTitle } from "../hooks/usePageTitle";

const MILESTONE_LABELS = {
  first_rescue: "First Rescue",
  active_rescuer: "Active Rescuer",
  campus_hero: "Campus Hero",
};

const MEDAL_COLORS = ["text-amber-400", "text-slate-400", "text-amber-600"];

function LeaderboardRow({ entry, index }) {
  const isTop3 = index < 3;
  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.04 }}
      className={`flex items-center gap-4 rounded-xl px-4 py-3 ${
        entry.is_me
          ? "border border-[color:var(--color-teal)] bg-[color:var(--color-teal)]/5"
          : "border border-transparent hover:bg-[color:var(--color-surface-raised)]"
      }`}
    >
      <span
        className={`w-7 text-center text-sm font-bold ${
          isTop3 ? MEDAL_COLORS[index] : "text-[color:var(--color-muted)]"
        }`}
      >
        {isTop3 ? <Medal className="h-4 w-4 inline" /> : `#${entry.rank}`}
      </span>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium truncate">
            {entry.display_name}
            {entry.is_me && (
              <span className="ml-1.5 text-xs text-[color:var(--color-teal)]">(you)</span>
            )}
          </span>
          {entry.milestone && (
            <span className="shrink-0 rounded-full bg-[color:var(--color-surface-raised)] px-2 py-0.5 text-xs text-[color:var(--color-muted)]">
              {MILESTONE_LABELS[entry.milestone] ?? entry.milestone}
            </span>
          )}
        </div>
        {entry.completion_rate != null && (
          <p className="text-xs text-[color:var(--color-muted)]">
            {Math.round(entry.completion_rate * 100)}% completion rate
          </p>
        )}
      </div>

      <div className="text-right shrink-0">
        <p className="font-semibold text-[color:var(--color-teal)]">{entry.item_count}</p>
        <p className="text-xs text-[color:var(--color-muted)]">items rescued</p>
      </div>
      <div className="text-right shrink-0 hidden sm:block">
        <p className="font-medium">${entry.total_value.toLocaleString("en-US", { maximumFractionDigits: 0 })}</p>
        <p className="text-xs text-[color:var(--color-muted)]">value</p>
      </div>
    </motion.div>
  );
}

function BuildingRow({ row, index }) {
  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.05 }}
      className="flex items-center gap-4 rounded-xl border border-[color:var(--color-border)] bg-[color:var(--color-surface)] px-4 py-3"
    >
      <Building2 className="h-5 w-5 shrink-0 text-[color:var(--color-muted)]" />
      <div className="flex-1 min-w-0">
        <p className="font-medium truncate">{row.building}</p>
        <p className="text-xs text-[color:var(--color-muted)]">
          {row.contributors} contributor{row.contributors !== 1 ? "s" : ""}
        </p>
      </div>
      <div className="text-right shrink-0">
        <p className="font-semibold text-[color:var(--color-teal)]">{row.item_count}</p>
        <p className="text-xs text-[color:var(--color-muted)]">items</p>
      </div>
      <div className="text-right shrink-0 hidden sm:block">
        <p className="font-medium">${row.total_value.toLocaleString("en-US", { maximumFractionDigits: 0 })}</p>
        <p className="text-xs text-[color:var(--color-muted)]">value</p>
      </div>
    </motion.div>
  );
}

function ErrorPanel({ message, onRetry }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3 text-center rounded-2xl border border-black/8 dark:border-white/8 bg-[color:var(--color-surface)]">
      <p className="text-sm text-[color:var(--color-muted)]">{message}</p>
      <button
        onClick={onRetry}
        className="flex items-center gap-1.5 text-xs font-semibold text-[color:var(--color-teal)] hover:opacity-80"
      >
        <RefreshCw size={13} /> Try again
      </button>
    </div>
  );
}

export default function LeaderboardPage() {
  usePageTitle("Leaderboard — ReNest");

  const [tab, setTab] = useState("people");
  const [period, setPeriod] = useState("all");

  const {
    data: peopleData,
    loading: loadingPeople,
    error: peopleError,
    refetch: refetchPeople,
  } = useApi(tab === "people" ? `/leaderboard?period=${period}` : null);

  const {
    data: buildingsData,
    loading: loadingBuildings,
    error: buildingsError,
    refetch: refetchBuildings,
  } = useApi(tab === "buildings" ? "/leaderboard/buildings" : null);

  const board = peopleData?.leaderboard ?? [];
  const myRank = peopleData?.my_rank ?? null;
  const buildings = buildingsData?.buildings ?? [];
  const loading = tab === "people" ? loadingPeople : loadingBuildings;
  const error = tab === "people" ? peopleError : buildingsError;
  const refetch = tab === "people" ? refetchPeople : refetchBuildings;

  return (
    <div className="mx-auto max-w-2xl px-4 py-10 space-y-6">
      <div className="flex items-center gap-3">
        <Trophy className="h-7 w-7 text-amber-400" />
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Campus Leaderboard</h1>
          <p className="text-sm text-[color:var(--color-muted)]">
            Students making the biggest impact this move-out season
          </p>
        </div>
      </div>

      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex rounded-xl border border-[color:var(--color-border)] overflow-hidden text-sm">
          {[
            { key: "people", label: "Rescuers", icon: Trophy },
            { key: "buildings", label: "Buildings", icon: Building2 },
          ].map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`flex items-center gap-1.5 px-4 py-2 transition-colors ${
                tab === key
                  ? "bg-[color:var(--color-teal)] text-white font-medium"
                  : "text-[color:var(--color-muted)] hover:text-[color:var(--color-fg)]"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>

        {tab === "people" && (
          <div className="flex rounded-xl border border-[color:var(--color-border)] overflow-hidden text-sm">
            {[
              { key: "all", label: "All time" },
              { key: "semester", label: "This semester" },
            ].map(({ key, label }) => (
              <button
                key={key}
                onClick={() => setPeriod(key)}
                className={`px-4 py-2 transition-colors ${
                  period === key
                    ? "bg-[color:var(--color-surface-raised)] font-medium"
                    : "text-[color:var(--color-muted)] hover:text-[color:var(--color-fg)]"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      {loading ? (
        <div className="space-y-2">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="h-14 rounded-xl animate-pulse bg-[color:var(--color-surface)]" />
          ))}
        </div>
      ) : error ? (
        <ErrorPanel message={error} onRetry={() => refetch()} />
      ) : tab === "people" ? (
        <>
          {board.length === 0 ? (
            <p className="text-center py-16 text-[color:var(--color-muted)]">
              No data yet — be the first rescuer on your campus!
            </p>
          ) : (
            <div className="space-y-1">
              {board.map((entry, i) => (
                <LeaderboardRow key={entry.rank} entry={entry} index={i} />
              ))}
            </div>
          )}
          {myRank && (
            <p className="text-center text-sm text-[color:var(--color-muted)]">
              You&apos;re ranked <span className="font-semibold text-[color:var(--color-fg)]">#{myRank}</span> on your campus
            </p>
          )}
          <p className="text-center text-xs text-[color:var(--color-muted)]">
            <Link to="/settings" className="font-semibold text-[color:var(--color-tag)] hover:opacity-70">
              Opt out of the leaderboard
            </Link>{" "}
            any time in your account settings.
          </p>
        </>
      ) : (
        <>
          {buildings.length === 0 ? (
            <p className="text-center py-16 text-[color:var(--color-muted)]">
              No building data yet — add your building when you post a listing!
            </p>
          ) : (
            <div className="space-y-2">
              {buildings.map((row, i) => (
                <BuildingRow key={row.building} row={row} index={i} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
