import { Flame } from "lucide-react";
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { apiFetch } from "../lib/api";
import { cn } from "../lib/cn";

const CATEGORY_COLORS = {
  storage: "bg-amber-100 text-amber-700",
  lighting: "bg-yellow-100 text-yellow-700",
  supplies: "bg-blue-100 text-blue-700",
  comfort: "bg-purple-100 text-purple-700",
  toiletries: "bg-teal-100 text-teal-700",
  decor: "bg-pink-100 text-pink-700",
  other: "bg-gray-100 text-gray-600",
};

export default function TrendingStrip() {
  const [items, setItems] = useState([]);

  useEffect(() => {
    apiFetch("/listings/trending")
      .then((data) => setItems(data.results ?? []))
      .catch(() => {});
  }, []);

  if (!items.length) return null;

  return (
    <div className="mx-auto max-w-[1200px] px-4 md:px-8 py-3">
      <div className="flex items-center gap-2 mb-2">
        <Flame size={14} className="text-orange-500" />
        <span className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          Moving fast on campus
        </span>
      </div>
      <div
        className="flex gap-3 overflow-x-auto pb-1"
        style={{ scrollbarWidth: "none" }}
      >
        {items.map((item) => (
          <Link
            key={item.id}
            to={`/listings/${item.id}`}
            className="flex-shrink-0 flex items-center gap-2.5 rounded-xl border border-[var(--color-border)] bg-[var(--color-surface-raised)] px-3 py-2 hover:border-[var(--color-primary-muted)] transition-colors"
          >
            {item.image ? (
              <img
                src={item.image}
                alt={item.title}
                className="w-8 h-8 rounded-lg object-cover flex-shrink-0"
                loading="lazy"
              />
            ) : (
              <div className="w-8 h-8 rounded-lg bg-[var(--color-border)] flex-shrink-0" />
            )}
            <div className="min-w-0">
              <p className="text-xs font-medium text-[var(--color-text)] truncate max-w-[120px]">
                {item.title}
              </p>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span
                  className={cn(
                    "text-[10px] px-1.5 py-0.5 rounded-full font-medium",
                    CATEGORY_COLORS[item.category] ?? CATEGORY_COLORS.other
                  )}
                >
                  {item.category}
                </span>
                <span className="text-[10px] text-[var(--color-text-muted)]">
                  {item.price_type === "free" ? "Free" : `$${parseFloat(item.price_amount).toFixed(0)}`}
                </span>
              </div>
            </div>
            <div className="flex items-center gap-0.5 flex-shrink-0 ml-1">
              <Flame size={10} className="text-orange-400" />
              <span className="text-[10px] text-orange-500 font-semibold">{item.view_count}</span>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
