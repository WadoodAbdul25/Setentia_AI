import * as esbuild from "esbuild";

const watch = process.argv.includes("--watch");
const context = await esbuild.context({
  bundle: true,
  entryPoints: ["src/extension.ts"],
  external: ["vscode"],
  format: "cjs",
  logLevel: "info",
  minify: !watch,
  outfile: "dist/extension.js",
  platform: "node",
  sourcemap: watch,
  target: "node20",
});

if (watch) {
  await context.watch();
} else {
  await context.rebuild();
  await context.dispose();
}
