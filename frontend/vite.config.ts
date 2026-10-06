import { defineConfig } from "vite";

export default defineConfig({
  build: {
    rollupOptions: {
      input: {
        main: "index.html",
        intro: "intro.html",
        workspace: "workspace.html",
        history: "history.html",
        issues: "issues.html",
        view: "view.html",
        home: "home.html",
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
      "/asr": {
        target: "http://127.0.0.1:9000",
        ws: true,
        rewrite: (path: string) => path.replace(/^\/asr/, ""),
      },
    },
  },
});
