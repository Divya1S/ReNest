import { Star } from "lucide-react";
import React from "react";

import { cn } from "../lib/cn";

export default function RatingStars({
  rating = 0,
  size = 16,
  className,
  activeClassName = "fill-amber-400 text-amber-400",
  inactiveClassName = "text-slate-300 dark:text-slate-600",
}) {
  return (
    <div
      role="img"
      aria-label={`Rating ${rating} out of 5`}
      className={cn("inline-flex items-center gap-1", className)}
    >
      {Array.from({ length: 5 }, (_, index) => {
        const filled = index < Math.round(Number(rating) || 0);
        return (
          <Star
            key={index}
            size={size}
            aria-hidden="true"
            className={filled ? activeClassName : inactiveClassName}
          />
        );
      })}
    </div>
  );
}
