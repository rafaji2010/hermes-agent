/**
 * Whiteboard Plugin — Dark-mode detection
 *
 * The Hermes desktop app flags dark mode by toggling the `dark` class on
 * `<html>` (the same signal `use-is-dark.ts` in core reads). Plugins cannot
 * import `@/…` internals, so this mirrors that tiny hook against the SDK-only
 * boundary: read the class, then follow it via a MutationObserver.
 */

import { useEffect, useState } from 'react'

const isDarkNow = () => typeof document !== 'undefined' && document.documentElement.classList.contains('dark')

export function useIsDark(): boolean {
  const [dark, setDark] = useState(isDarkNow)

  useEffect(() => {
    const root = document.documentElement
    const observer = new MutationObserver(() => setDark(isDarkNow()))

    observer.observe(root, { attributes: true, attributeFilter: ['class'] })
    setDark(isDarkNow())

    return () => observer.disconnect()
  }, [])

  return dark
}
