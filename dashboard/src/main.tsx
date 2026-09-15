import React from "react";
import ReactDOM from "react-dom/client";
import { loadRuntimeConfig } from "./runtimeConfig";

// App (and its transitive imports, e.g. lib/api.ts) reads getRuntimeConfig()
// at module scope, so it must not be evaluated until loadRuntimeConfig() has
// populated window.__ENV__ — a static `import App from "./App"` here would
// run before loadRuntimeConfig() resolves, defeating the await. Use a
// dynamic import instead.
loadRuntimeConfig().then(async () => {
  const { default: App } = await import("./App");
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
});
