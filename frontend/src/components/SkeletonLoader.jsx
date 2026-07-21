import React from "react";

import { cn } from "../lib/cn";

export default function SkeletonLoader({ className, ...props }) {
  return (
    <div
      aria-hidden="true"
      className={cn("shimmer-skeleton", className)}
      {...props}
    />
  );
}
