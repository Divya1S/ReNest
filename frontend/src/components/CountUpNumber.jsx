import { useSpring, useMotionValueEvent } from "framer-motion";
import React, { useEffect, useState } from "react";

export default function CountUpNumber({ value, prefix = "", suffix = "" }) {
  const [display, setDisplay] = useState("0");
  const spring = useSpring(0, {
    stiffness: 100,
    damping: 30,
    restDelta: 0.001
  });

  useMotionValueEvent(spring, "change", (latest) => {
    setDisplay(Math.floor(latest).toLocaleString());
  });

  useEffect(() => {
    spring.set(value);
  }, [value, spring]);

  return (
    <span aria-label={`${prefix}${Number(value).toLocaleString()}${suffix}`}>
      <span aria-hidden="true">
        {prefix}
        {display}
        {suffix}
      </span>
    </span>
  );
}
