import js from "@eslint/js";
import globals from "globals";
import reactPlugin from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import importPlugin from "eslint-plugin-import";

export default [
  js.configs.recommended,
  // ── Source files ──────────────────────────────────────────────────────────
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
    rules: {
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
    },
  },
  // ── Test files — add vitest/jsdom globals ─────────────────────────────────
  {
    files: ["src/**/*.test.{js,jsx}"],
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
