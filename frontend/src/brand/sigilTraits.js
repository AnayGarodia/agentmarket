// Traits for AgentSigil. The component itself owns the visual rendering;
// this file just exposes the deterministic primary colour so other surfaces
// (hero accent on AgentDetailPage, etc.) can pick it up to stay coherent.

function djb2(str) {
  let h = 5381
  for (let i = 0; i < str.length; i++) {
    h = ((h << 5) + h) ^ str.charCodeAt(i)
    h = h | 0
  }
  return Math.abs(h)
}

function mulberry32(seed) {
  return function () {
    seed |= 0; seed = seed + 0x6D2B79F5 | 0
    let t = Math.imul(seed ^ seed >>> 15, 1 | seed)
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t
    return ((t ^ t >>> 14) >>> 0) / 4294967296
  }
}

// Mirror of AgentSigil PALETTE — keep these two arrays in sync.
const PALETTE = [
  { bg: '#063F43', fg: '#F4DDC3' },
  { bg: '#102B2F', fg: '#E8B89A' },
  { bg: '#C65F3F', fg: '#F8EFE0' },
  { bg: '#7A4F30', fg: '#F4DDC3' },
  { bg: '#6C8D6E', fg: '#0F1F1B' },
  { bg: '#3F4D8A', fg: '#F4EDDC' },
  { bg: '#C2342A', fg: '#FFFDF8' },
  { bg: '#1F1A14', fg: '#D9A661' },
]

export function getSigilTraits(agentId) {
  const seed = djb2(String(agentId))
  const rand = mulberry32(seed)
  const palette = PALETTE[Math.floor(rand() * PALETTE.length)]
  return {
    seed,
    primaryColor: palette.bg,
    accentColor:  palette.fg,
    colors: [palette.bg, palette.fg],
  }
}

export function getAgentColor(agentId) {
  return getSigilTraits(agentId).primaryColor
}
