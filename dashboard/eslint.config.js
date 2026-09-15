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
      "**/*.stories.ts",
      "**/*.stories.tsx",
      "src/lib/chartUtils.ts",
      "src/lib/i18n/index.ts",
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
