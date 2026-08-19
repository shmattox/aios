import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base must match the dashboard mount so built asset URLs are /pipeline/assets/*
export default defineConfig({
  base: "/pipeline/",
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
});
