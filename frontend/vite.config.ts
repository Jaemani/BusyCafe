import { defineConfig } from "vite";

export default defineConfig({
  server: {
    host: "127.0.0.1",
    port: 5188,
    strictPort: true,
    allowedHosts: [".tail2743ae.ts.net"],
  },
});
