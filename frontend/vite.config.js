import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // keepNames: esbuild's minifier renames every top-level function/component
  // by default (that's most of why a production crash trace used to read as
  // `at t (index-XYZ.js:1:23456)`). This preserves real .name at runtime —
  // React's error-boundary componentStack and Error.stack both read from
  // it — while everything else still minifies normally. Cheap: a few bytes
  // of __name() calls, not a readability-vs-size tradeoff worth making.
  esbuild: { keepNames: true },
  build: {
    // 'hidden': still emit .map files (needed for server-side resolution of
    // crash reports — see backend/orgtree/crashreports.py) but do NOT append
    // a //# sourceMappingURL comment to the shipped JS, so a browser's
    // devtools (or a random visitor) never auto-fetches full original source.
    // The postbuild step (package.json's "postbuild") then moves the .map
    // files OUT OF dist/ ENTIRELY, to a sibling frontend/sourcemaps/ — see
    // that script's own comment for why dist/sourcemaps/ was not enough
    // (api.py's SPA catch-all serves anything under dist/, not just /assets).
    sourcemap: 'hidden',
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:7360',
        ws: true,
      },
    },
  },
})
