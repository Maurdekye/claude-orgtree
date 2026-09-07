import * as esbuild from 'esbuild'
await esbuild.build({entryPoints:['tests/gitsettings.browser.tsx'],outfile:'node_modules/.orgtree-git-settings/probe.js',bundle:true,format:'esm',platform:'browser',target:'es2022',jsx:'automatic',define:{'process.env.NODE_ENV':'"production"'}})
