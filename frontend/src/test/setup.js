import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import React from "react";
import { expect } from "vitest";
import { afterEach, vi } from "vitest";
import { configureAxe } from "vitest-axe";
import * as axeMatchers from "vitest-axe/matchers";

expect.extend(axeMatchers);

// Axe defaults — skip rules that require a real layout engine, computed CSS,
// or a full page structure (unit tests render components without AppFrame).
export const axe = configureAxe({
  rules: {
    "scrollable-region-focusable": { enabled: false },
    "color-contrast": { enabled: false },
    // Unit tests render pages without AppFrame's <main> landmark wrapper.
    "region": { enabled: false },
  },
});

afterEach(() => {
  cleanup();
});

vi.mock("framer-motion", async () => {
  const componentCache = new Map();

  function getMotionComponent(tag) {
    if (!componentCache.has(tag)) {
      componentCache.set(
        tag,
        React.forwardRef(function MockMotionComponent(
          // eslint-disable-next-line no-unused-vars
          { children, initial, animate, exit, transition, variants, whileHover, whileTap, whileInView, layout, ...props },
          ref,
        ) {
          return React.createElement(tag, { ref, ...props }, children);
        }),
      );
    }
    return componentCache.get(tag);
  }

  const motion = new Proxy(
    {},
    {
      get(_target, tag) {
        return getMotionComponent(tag);
      },
    },
  );

  function useSpring(initialValue = 0) {
    let currentValue = Number(initialValue) || 0;
    const listeners = new Set();
    return {
      get() {
        return currentValue;
      },
      set(nextValue) {
        currentValue = Number(nextValue) || 0;
        listeners.forEach((listener) => listener(currentValue));
      },
      on(_eventName, listener) {
        listeners.add(listener);
        listener(currentValue);
        return () => listeners.delete(listener);
      },
    };
  }

  return {
    AnimatePresence: ({ children }) => children,
    MotionConfig: ({ children }) => children,
    motion,
    useInView: () => true,
    useReducedMotion: () => true,
    useSpring,
    useMotionValueEvent(value, _eventName, callback) {
      React.useEffect(() => {
        if (!value || typeof value.on !== "function") {
          return undefined;
        }
        return value.on("change", callback);
      }, [value, callback]);
    },
  };
});

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: () => ({
    matches: false,
    addListener: () => {},
    removeListener: () => {},
  }),
});

// jsdom doesn't implement scrollIntoView; provide a no-op stub.
if (!("scrollIntoView" in Element.prototype)) {
  Element.prototype.scrollIntoView = () => {};
}

// jsdom doesn't implement IntersectionObserver; provide a no-op stub so
// Framer Motion's useInView doesn't throw during unit tests.
if (!("IntersectionObserver" in window)) {
  window.IntersectionObserver = class IntersectionObserver {
    constructor(cb) {
      this._cb = cb;
    }
    observe(el) {
      // Immediately report the element as fully visible so animations resolve.
      this._cb([{ isIntersecting: true, target: el }], this);
    }
    unobserve() {}
    disconnect() {}
  };
}
