import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import frappeui from "frappe-ui/vite";
import { cpSync } from "node:fs";

export default defineConfig({
  base: "./",
  plugins: [
    frappeui({ frappeProxy: false, jinjaBootData: false, buildConfig: false }),
    vue(),
    {
      name: "client-assets",
      closeBundle() {
        cpSync(
          "../apps/passport/passport/public/ui",
          "../apps/passport_client/passport_client/public/ui",
          { recursive: true }
        );
      },
    },
  ],
  build: {
    outDir: "../apps/passport/passport/public/ui",
    emptyOutDir: false,
    manifest: "manifest.json",
    rollupOptions: {
      input: "src/main.js",
      preserveEntrySignatures: "strict",
      output: {
        entryFileNames: "passport.[hash].js",
        assetFileNames: "[name].[hash][extname]",
      },
    },
  },
});
