/**
 * Built-in desktop themes. Names match the CLI skins / dashboard presets.
 * Add new themes here — no code changes needed elsewhere.
 */

import type { HermesSkin } from '@hermes/shared/skin'

import { skinToDesktopTheme } from './skin'
import type { DesktopTheme, DesktopThemeTypography } from './types'

// Color-emoji fonts to append to every stack as a last resort. None of the UI
// text/mono fonts carry emoji glyphs, so without this emoji render as tofu
// boxes on platforms whose default text font lacks them (e.g. Linux/#40364).
// Covers macOS, Windows, Linux, plus the `emoji` generic for anything else.
export const EMOJI_FALLBACK = '"Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji", emoji'

const SYSTEM_SANS =
  '"Segoe WPC", "Segoe UI", -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", system-ui, sans-serif, ' +
  EMOJI_FALLBACK

const SYSTEM_MONO = 'Menlo, Monaco, "SF Mono", "Courier Prime", monospace, ' + EMOJI_FALLBACK

export const DEFAULT_TYPOGRAPHY: DesktopThemeTypography = { fontSans: SYSTEM_SANS, fontMono: SYSTEM_MONO }

const NOUS_BLUE = '#0053FD'
const PSYCHE_BLUE = '#1540B1'
const PSYCHE_WARM = '#FFE6CB'

const nousTint = (pct: number) => `color-mix(in srgb, ${NOUS_BLUE} ${pct}%, #FFFFFF)`
const nousTintTransparent = (pct: number) => `color-mix(in srgb, ${NOUS_BLUE} ${pct}%, transparent)`

/**
 * Nous — canonical Hermes desktop identity. The palette keeps the current
 * glass geometry neutral, then lets the old bb/gui blue and psyche cream
 * return as accent seeds.
 */
export const nousTheme: DesktopTheme = {
  name: 'nous',
  label: 'Nous',
  description: 'Glass neutrals with Nous blue accents',
  colors: {
    background: '#F8FAFF',
    foreground: '#17171A',
    card: '#FFFFFF',
    cardForeground: '#17171A',
    muted: nousTint(5),
    mutedForeground: '#666678',
    popover: '#FFFFFF',
    popoverForeground: '#17171A',
    primary: NOUS_BLUE,
    primaryForeground: '#FCFCFC',
    secondary: nousTint(7),
    secondaryForeground: '#242432',
    accent: nousTint(10),
    accentForeground: '#202030',
    border: nousTintTransparent(22),
    input: nousTintTransparent(30),
    ring: NOUS_BLUE,
    midground: NOUS_BLUE,
    composerRing: NOUS_BLUE,
    destructive: '#C72E4D',
    destructiveForeground: '#FFFFFF',
    sidebarBackground: '#F3F7FF',
    sidebarBorder: nousTintTransparent(18),
    userBubble: nousTint(6),
    userBubbleBorder: nousTintTransparent(24)
  },
  darkColors: {
    background: '#0D2F86',
    foreground: PSYCHE_WARM,
    card: '#12378F',
    cardForeground: PSYCHE_WARM,
    muted: '#183F9A',
    mutedForeground: '#B5C7F3',
    popover: '#123A96',
    popoverForeground: PSYCHE_WARM,
    primary: PSYCHE_WARM,
    primaryForeground: '#0D2F86',
    secondary: '#1B45A4',
    secondaryForeground: '#E0E8FF',
    accent: PSYCHE_BLUE,
    accentForeground: '#F0F4FF',
    border: '#3158AD',
    input: '#0B2566',
    ring: PSYCHE_WARM,
    midground: NOUS_BLUE,
    composerRing: PSYCHE_WARM,
    destructive: '#C0473A',
    destructiveForeground: '#FEF2F2',
    sidebarBackground: '#09286F',
    sidebarBorder: '#234A9C',
    userBubble: '#143B91',
    userBubbleBorder: '#3A63BD'
  },
  typography: {
    fontSans: SYSTEM_SANS,
    fontMono: SYSTEM_MONO,
    fontUrl: 'https://fonts.googleapis.com/css2?family=Courier+Prime:wght@400;700&display=swap'
  }
}

/** Deep blue-violet with cool accents. Matches the dashboard midnight theme. */
export const midnightTheme: DesktopTheme = {
  name: 'midnight',
  label: 'Midnight',
  description: 'Deep blue-violet with cool accents',
  colors: {
    background: '#08081c',
    foreground: '#ddd6ff',
    card: '#0d0d28',
    cardForeground: '#ddd6ff',
    muted: '#13133a',
    mutedForeground: '#7c7ab0',
    popover: '#0f0f2e',
    popoverForeground: '#ddd6ff',
    primary: '#ddd6ff',
    primaryForeground: '#08081c',
    secondary: '#1a1a4a',
    secondaryForeground: '#c4bff0',
    accent: '#1a1a44',
    accentForeground: '#d0c8ff',
    border: '#1e1e52',
    input: '#1e1e52',
    ring: '#8b80e8',
    midground: '#8b80e8',
    destructive: '#b03060',
    destructiveForeground: '#fef2f2',
    sidebarBackground: '#06061a',
    sidebarBorder: '#12123a',
    userBubble: '#14143a',
    userBubbleBorder: '#242466'
  },
  typography: {
    fontMono: `"JetBrains Mono", ${SYSTEM_MONO}`,
    fontUrl: 'https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap'
  }
}

/** Warm crimson and bronze — forge vibes. Matches the CLI ares skin. */
export const emberTheme: DesktopTheme = {
  name: 'ember',
  label: 'Ember',
  description: 'Warm crimson and bronze — forge vibes',
  colors: {
    background: '#160800',
    foreground: '#ffd8b0',
    card: '#1e0e04',
    cardForeground: '#ffd8b0',
    muted: '#2a1408',
    mutedForeground: '#aa7a56',
    popover: '#221008',
    popoverForeground: '#ffd8b0',
    primary: '#ffd8b0',
    primaryForeground: '#160800',
    secondary: '#341800',
    secondaryForeground: '#f0c090',
    accent: '#301600',
    accentForeground: '#e8c080',
    border: '#3a1c08',
    input: '#3a1c08',
    ring: '#d97316',
    midground: '#d97316',
    destructive: '#c43010',
    destructiveForeground: '#fef2f2',
    sidebarBackground: '#100600',
    sidebarBorder: '#2a1004',
    userBubble: '#2a1000',
    userBubbleBorder: '#4a2010'
  },
  typography: {
    fontMono: `"IBM Plex Mono", ${SYSTEM_MONO}`,
    fontUrl: 'https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;700&display=swap'
  }
}

/** Clean grayscale. Matches the CLI mono skin and dashboard mono theme. */
export const monoTheme: DesktopTheme = {
  name: 'mono',
  label: 'Mono',
  description: 'Clean grayscale — minimal and focused',
  colors: {
    background: '#0e0e0e',
    foreground: '#eaeaea',
    card: '#141414',
    cardForeground: '#eaeaea',
    muted: '#1e1e1e',
    mutedForeground: '#808080',
    popover: '#181818',
    popoverForeground: '#eaeaea',
    primary: '#eaeaea',
    primaryForeground: '#0e0e0e',
    secondary: '#262626',
    secondaryForeground: '#c8c8c8',
    accent: '#222222',
    accentForeground: '#d8d8d8',
    border: '#2a2a2a',
    input: '#2a2a2a',
    ring: '#9a9a9a',
    midground: '#9a9a9a',
    destructive: '#a84040',
    destructiveForeground: '#fef2f2',
    sidebarBackground: '#0a0a0a',
    sidebarBorder: '#202020',
    userBubble: '#1a1a1a',
    userBubbleBorder: '#363636'
  }
}

/** Neon green on black. Matches the CLI cyberpunk skin and dashboard theme. */
export const cyberpunkTheme: DesktopTheme = {
  name: 'cyberpunk',
  label: 'Cyberpunk',
  description: 'Neon green on black — matrix terminal',
  colors: {
    background: '#000a00',
    foreground: '#00ff41',
    card: '#001200',
    cardForeground: '#00ff41',
    muted: '#001a00',
    mutedForeground: '#1a8a30',
    popover: '#001000',
    popoverForeground: '#00ff41',
    primary: '#00ff41',
    primaryForeground: '#000a00',
    secondary: '#002800',
    secondaryForeground: '#00cc34',
    accent: '#002000',
    accentForeground: '#00e038',
    border: '#003000',
    input: '#003000',
    ring: '#00ff41',
    midground: '#00ff41',
    destructive: '#ff003c',
    destructiveForeground: '#000a00',
    sidebarBackground: '#000600',
    sidebarBorder: '#001800',
    userBubble: '#001400',
    userBubbleBorder: '#004800'
  },
  typography: {
    fontMono: `"Courier New", Courier, monospace, ${EMOJI_FALLBACK}`,
    fontSans: `"Courier New", Courier, monospace, ${EMOJI_FALLBACK}`
  }
}

/** Cool slate blue for developers. Matches the CLI slate skin. */
export const slateTheme: DesktopTheme = {
  name: 'slate',
  label: 'Slate',
  description: 'Cool slate blue — focused developer theme',
  colors: {
    background: '#0d1117',
    foreground: '#c9d1d9',
    card: '#161b22',
    cardForeground: '#c9d1d9',
    muted: '#21262d',
    mutedForeground: '#8b949e',
    popover: '#1c2128',
    popoverForeground: '#c9d1d9',
    primary: '#c9d1d9',
    primaryForeground: '#0d1117',
    secondary: '#2a3038',
    secondaryForeground: '#adb5bf',
    accent: '#1e2530',
    accentForeground: '#c0c8d0',
    border: '#30363d',
    input: '#30363d',
    ring: '#58a6ff',
    midground: '#58a6ff',
    destructive: '#cf4848',
    destructiveForeground: '#fef2f2',
    sidebarBackground: '#090d13',
    sidebarBorder: '#1c2228',
    userBubble: '#1e2a38',
    userBubbleBorder: '#2e4060'
  },
  typography: {
    fontMono: `"JetBrains Mono", ${SYSTEM_MONO}`
  }
}

// ── Curated dark skins (hermes-skins-pack · bchop-studio · MIT) ─────────────
// Converted through the same skinToDesktopTheme path the backend sync uses, so
// each built-in's desktop palette is byte-identical to its live-synced form.
// Seed colors are the pack's load-bearing tokens; labels/descriptions are
// humanized here since the converter only title-cases the name.
const CURATED_DARK_SKINS: HermesSkin[] = [
  {
    name: 'neon-ghost',
    colors: {
      background: '#0a0a0f', status_bar_bg: '#05050a', ui_text: '#e0e0ff', banner_text: '#e0e0ff',
      status_bar_text: '#c0c0d0', ui_accent: '#ff00ff', banner_accent: '#ff00ff', banner_title: '#00ffff',
      ui_border: '#ff00ff', banner_border: '#ff00ff', banner_dim: '#7d3c98', session_border: '#7d3c98',
      ui_error: '#ff3366', completion_menu_bg: '#0a0a0f'
    }
  },
  {
    name: 'midnight-studio',
    colors: {
      background: '#1a1a2e', status_bar_bg: '#121224', ui_text: '#d8d0c8', banner_text: '#d8d0c8',
      status_bar_text: '#a09888', ui_accent: '#c8a050', banner_accent: '#c8a050', banner_title: '#e8d080',
      ui_border: '#504868', banner_border: '#c8a050', banner_dim: '#504868', session_border: '#504868',
      ui_error: '#b05850', completion_menu_bg: '#1a1a2e'
    }
  },
  {
    name: 'ultraviolet',
    colors: {
      background: '#10061c', status_bar_bg: '#08030e', ui_text: '#e8dcf4', banner_text: '#e8dcf4',
      status_bar_text: '#e8dcf4', ui_accent: '#b45cff', banner_accent: '#b45cff', banner_title: '#b45cff',
      ui_border: '#38204e', banner_border: '#38204e', banner_dim: '#6f5a88', session_border: '#28163a',
      ui_error: '#ff5c88', completion_menu_bg: '#10061c'
    }
  },
  {
    name: 'chrome-rain',
    colors: {
      background: '#120c2e', status_bar_bg: '#0a081c', ui_text: '#e8def8', banner_text: '#e8def8',
      status_bar_text: '#c8c0d8', ui_accent: '#e6398c', banner_accent: '#e6398c', banner_title: '#4dc9f6',
      ui_border: '#4dc9f6', banner_border: '#e6398c', banner_dim: '#6b5fa0', session_border: '#6b5fa0',
      ui_error: '#e74c3c', completion_menu_bg: '#120c2e'
    }
  },
  {
    name: 'void-sunset',
    colors: {
      background: '#1a1030', status_bar_bg: '#0f0820', ui_text: '#e6d8f0', banner_text: '#e6d8f0',
      status_bar_text: '#c8b8d8', ui_accent: '#ff5fd2', banner_accent: '#ff5fd2', banner_title: '#ffb870',
      ui_border: '#ffb870', banner_border: '#ff5fd2', banner_dim: '#7a5fa0', session_border: '#7a5fa0',
      ui_error: '#ff5fd2', completion_menu_bg: '#1a1030'
    }
  },
  {
    name: 'nebula-drift',
    colors: {
      background: '#120e1e', status_bar_bg: '#09070f', ui_text: '#ded8ec', banner_text: '#ded8ec',
      status_bar_text: '#ded8ec', ui_accent: '#a878e8', banner_accent: '#a878e8', banner_title: '#a878e8',
      ui_border: '#342c48', banner_border: '#342c48', banner_dim: '#6c6482', session_border: '#262037',
      ui_error: '#e06080', completion_menu_bg: '#120e1e'
    }
  },
  {
    name: 'deep-void',
    colors: {
      background: '#000000', status_bar_bg: '#000000', ui_text: '#b0a0c0', banner_text: '#b0a0c0',
      status_bar_text: '#807090', ui_accent: '#7060a0', banner_accent: '#7060a0', banner_title: '#9080c0',
      ui_border: '#3a3050', banner_border: '#3a3050', banner_dim: '#2a2040', session_border: '#2a2040',
      ui_error: '#904040', completion_menu_bg: '#050508'
    }
  },
  {
    name: 'obsidian',
    colors: {
      background: '#0f0f0f', status_bar_bg: '#080808', ui_text: '#d0d0d0', banner_text: '#d0d0d0',
      status_bar_text: '#a0a0a0', ui_accent: '#787878', banner_accent: '#787878', banner_title: '#a0a0a0',
      ui_border: '#555555', banner_border: '#555555', banner_dim: '#444444', session_border: '#444444',
      ui_error: '#c05050', completion_menu_bg: '#0f0f0f'
    }
  },
  {
    name: 'eclipse',
    colors: {
      background: '#18181a', status_bar_bg: '#101012', ui_text: '#d0d0d8', banner_text: '#d0d0d8',
      status_bar_text: '#9898a0', ui_accent: '#9a88c0', banner_accent: '#9a88c0', banner_title: '#c8b8e8',
      ui_border: '#6a5a8a', banner_border: '#6a5a8a', banner_dim: '#3a3050', session_border: '#3a3050',
      ui_error: '#b86868', completion_menu_bg: '#18181a'
    }
  }
]

const CURATED_THEME_META: Record<string, { label: string; description: string }> = {
  'neon-ghost': { label: 'Neon Ghost', description: 'Electric ghost in the machine — hot magenta on void black' },
  'midnight-studio': { label: 'Midnight Studio', description: 'Late-night coding den — dark indigo with warm gold accents' },
  ultraviolet: { label: 'Ultraviolet', description: 'Club dark under blacklight — lilac text, violet-laser accents' },
  'chrome-rain': { label: 'Chrome Rain', description: 'Wet chrome streets at midnight — electric blue and hot pink' },
  'void-sunset': { label: 'Void Sunset', description: 'Neon sunset bleeding into a dead channel — violet and tangerine' },
  'nebula-drift': { label: 'Nebula Drift', description: 'Interstellar gas — lavender text with violet accents' },
  'deep-void': { label: 'Deep Void', description: 'True AMOLED black with barely-there violet undertones' },
  obsidian: { label: 'Obsidian', description: 'Polished black glass — cool silver text and crimson alerts' },
  eclipse: { label: 'Eclipse', description: 'Total solar eclipse — charcoal with a soft lilac corona' }
}

const curatedDarkThemes: DesktopTheme[] = CURATED_DARK_SKINS.map(skin => skinToDesktopTheme(skin))
  .filter((t): t is DesktopTheme => t !== null)
  .map(t => ({ ...t, ...CURATED_THEME_META[t.name] }))

export const BUILTIN_THEMES: Record<string, DesktopTheme> = {
  nous: nousTheme,
  midnight: midnightTheme,
  ember: emberTheme,
  mono: monoTheme,
  cyberpunk: cyberpunkTheme,
  slate: slateTheme,
  ...Object.fromEntries(curatedDarkThemes.map(t => [t.name, t]))
}

export const BUILTIN_THEME_LIST = Object.values(BUILTIN_THEMES)

/** Skin used when nothing is persisted or the persisted name is retired. */
export const DEFAULT_SKIN_NAME = 'nous'
