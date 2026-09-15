import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import storybook from "eslint-plugin-storybook";
import globals from "globals";

export default tseslint.config(
  {
    ignores: [
      "**/dist/**",
      "**/node_modules/**",
      "**/.storybook/**",
      "**/storybook-static/**",
      "**/.storybook-cache/**",
      "**/build/**",
      "**/.svelte-kit/**",
      "**/src/hooks*.ts",
      "**/scripts/**",
      "**/*.config.ts",
      "**/*.config.js",
      // Dead Svelte-era `*.stories.ts` files (importing deleted `.svelte`
      // components) still sit alongside the new `*.stories.tsx` React
      // stories until Task 19's big-bang deletion of the old tree — ignore
      // only the `.ts` ones so the real `.stories.tsx` files get linted
      // like any other source file (mirrors the `.tsx`-only glob in
      // .storybook/main.ts, same reason).
      "**/*.stories.ts",
      "src/lib/stores.ts",
      "src/routes/+layout.ts",
      "src/routes/+page.ts",
      "src/routes/*/+layout.ts",
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/main.tsx", "src/App.tsx", "src/**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
    },
  },
  ...storybook.configs["flat/recommended"],
);
