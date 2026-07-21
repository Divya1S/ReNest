import { motion, useInView } from "framer-motion";
import React, { useRef } from "react";

import { cn } from "../lib/cn";

/**
 * Wraps content in a scroll-triggered entrance animation.
 * The animation fires once when the element enters the viewport
 * (with a -60px bottom margin so it triggers slightly before it's fully visible).
 * Uses Framer Motion's useInView so off-screen sections don't all animate
 * simultaneously on mount.
 */
export default function PageSection({ children, className, delay = 0, ...props }) {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-60px 0px" });

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 18 }}
      animate={isInView ? { opacity: 1, y: 0 } : { opacity: 0, y: 18 }}
      transition={{ duration: 0.45, ease: "easeOut", delay }}
      className={cn(className)}
      {...props}
    >
      {children}
    </motion.div>
  );
}
