import type { Preview } from "@storybook/react-vite";
import { initialize, mswLoader } from "msw-storybook-addon";
import "../src/app.css";

// Initialize MSW
initialize();

const preview: Preview = {
  parameters: {
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },

    a11y: {
      config: {
        rules: [
          {
            // Allow form labels without explicit control association for certain patterns
            id: "label",
            enabled: true,
          },
        ],
      },
    },
  },
  loaders: [mswLoader],
};

export default preview;
