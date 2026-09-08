import js from "@eslint/js";
import globals from "globals";
import reactPlugin from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import importPlugin from "eslint-plugin-import";
import tseslint from "typescript-eslint";

const sharedRules = {
  ...reactPlugin.configs.recommended.rules,
  "react/react-in-jsx-scope": "off",
  "react/prop-types": "off",
  // The two classic react-hooks rules (not the full v5 recommended set)
  "react-hooks/rules-of-hooks": "error",
  "react-hooks/exhaustive-deps": "warn",
  "no-console": "warn",
  "import/order": [
    "warn",
    {
      groups: ["builtin", "external", "internal", "parent", "sibling", "index"],
      "newlines-between": "always",
      alphabetize: { order: "asc", caseInsensitive: true },
    },
  ],
};

export default [
  js.configs.recommended,
  // ── JavaScript source ─────────────────────────────────────────────────────
  {
    files: ["src/**/*.{js,jsx}"],
    plugins: {
      react: reactPlugin,
      "react-hooks": reactHooks,
      import: importPlugin,
    },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.es2022,
      },
    },
    settings: {
      react: { version: "detect" },
    },
    rules: sharedRules,
  },
  // ── TypeScript source ─────────────────────────────────────────────────────
  // Without this block ESLint silently skipped every .ts/.tsx file, which is
  // where the largest pages live (Browse, Dashboard, Handoff Hub, Listing
  // detail/form), so CI's `--max-warnings 0` gate covered none of them.
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: {
      parser: tseslint.parser,
      ecmaVersion: 2022,
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.es2022,
      },
    },
    plugins: {
      "@typescript-eslint": tseslint.plugin,
      react: reactPlugin,
      "react-hooks": reactHooks,
      import: importPlugin,
    },
    settings: {
      react: { version: "detect" },
    },
    rules: {
      ...sharedRules,
      // tsc already reports undefined identifiers and unused locals with full
      // type information; the core rules produce false positives on TS syntax.
      "no-undef": "off",
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  // ── Test files — add vitest/jsdom globals ─────────────────────────────────
  {
    files: ["src/**/*.test.{js,jsx,ts,tsx}"],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.es2022,
        global: "readonly",
      },
    },
  },
  {
    ignores: ["dist/**", "node_modules/**", "e2e/**", "*.config.js"],
  },
];
