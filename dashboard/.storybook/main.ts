import type { StorybookConfig } from "@storybook/react-vite";

const config: StorybookConfig = {
  // Old Svelte-era `*.stories.ts` files (referencing deleted `.svelte`
  // components) still coexist on disk with the new `.tsx` stories until
  // Task 19's big-bang deletion of the Svelte tree — glob only `.tsx` to
  // avoid indexing the dead `.ts` duplicates (same story IDs, hard error).
  stories: ["../src/**/*.stories.tsx"],
  addons: ["@storybook/addon-a11y", "@storybook/addon-docs"],
  framework: {
    name: "@storybook/react-vite",
    options: {},
  },
};
export default config;
