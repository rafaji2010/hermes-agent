import { existsSync, statSync, readdirSync } from "fs"
import { resolve, join, relative } from "path"

const app = resolve(import.meta.dirname, "..")
const srcRoots = [join(app, "src"), join(app, "electron")]

/**
 * Vite (and TS) resolves `@/lib/chat-messages` to `chat-messages.ts` in
 * preference to `chat-messages/` directory. After a `chat-messages.ts ->
 * chat-messages/` split, a stale untracked `chat-messages.ts` left on disk
 * shadows the real module and the build fails with MISSING_EXPORT.
 *
 * This killed `hermes desktop` on every restart: the content-hash stamp
 * forces a rebuild whenever source moves, so the broken resolve was hit
 * every launch until the stale file was removed.
 */

function* walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === "dist" || entry.name === ".vite") continue
      yield* walk(full)
    } else if (entry.isFile() && (entry.name.endsWith(".ts") || entry.name.endsWith(".tsx"))) {
      yield full
    }
  }
}

const shadows = []

for (const root of srcRoots) {
  if (!existsSync(root)) continue
  for (const file of walk(root)) {
    const base = file.replace(/\.(ts|tsx)$/, "")
    // `foo.ts` shadows `foo/` if both exist
    if (existsSync(base) && statSync(base).isDirectory()) {
      shadows.push({
        file: relative(app, file),
        dir: relative(app, base) + "/",
      })
    }
  }
}

if (shadows.length) {
  console.error("\n✗ Shadowed modules — a .ts file shadows a directory with the same name:\n")
  for (const { file, dir } of shadows) {
    console.error(`  • ${file}  shadows  ${dir}`)
    console.error(`    Vite resolves \"@/...\" to the file, so the directory's exports are invisible.`)
    console.error(`    Fix: rm ${file}  (it is the stale pre-split leftover; the directory is the source of truth)\n`)
  }
  console.error("  This is the bug that made `hermes desktop` fail on every restart after the")
  console.error("  chat-messages / gateway-event splits (aug 2026) — a rebuild was forced by the")
  console.error("  content-hash stamp and the stale file produced MISSING_EXPORT.\n")
  process.exit(1)
}
