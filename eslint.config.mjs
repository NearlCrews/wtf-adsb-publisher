import js from "@eslint/js";
import globals from "globals";

export default [
  { ignores: [".private/**", "**/node_modules/**", "artifacts/**"] },
  {
    ...js.configs.recommended,
    files: ["themes/**/*.mjs"],
    languageOptions: { globals: globals.browser },
    rules: { ...js.configs.recommended.rules, "prefer-const": "error" },
  },
  {
    ...js.configs.recommended,
    files: ["themes/**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "script",
      globals: globals.browser,
    },
    linterOptions: { reportUnusedDisableDirectives: "error" },
    rules: { ...js.configs.recommended.rules, "prefer-const": "error" },
  },
  {
    ...js.configs.recommended,
    files: ["*.mjs", "tools/**/*.mjs"],
    languageOptions: { globals: globals.node },
    linterOptions: { reportUnusedDisableDirectives: "error" },
    rules: { ...js.configs.recommended.rules, "prefer-const": "error" },
  },
  {
    // Playwright callbacks in the theme checks run in the browser.
    files: ["tools/ghost-theme/check-*.mjs"],
    languageOptions: { globals: globals.browser },
  },
];
