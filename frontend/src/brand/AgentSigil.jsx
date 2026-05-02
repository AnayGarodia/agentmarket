// AgentSigil — deterministic identicon for every agent.
//
// Design: a 5×5 mirrored grid (à la GitHub identicons) on a solid brand-tinted
// canvas, with an optional center motif. Same agent_id → same sigil, every
// time. The palette is curated to Aztea brand tokens so every sigil reads on
// brand and contrasts cleanly against both light and dark surfaces.
//
// Width of the design space:
//   palette  ×  filledness  ×  pattern  ×  motif
//      8     ×       3      ×   2¹⁵≈big ×   5    ≈ enormous variety
//
// The grid is symmetric vertically (column n mirrors column 4−n) so every
// sigil feels balanced and architectural, not random noise.

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

// 8 curated brand-tinted backgrounds. Each pair is { bg, fg } where fg is the
// pattern colour — chosen for legible contrast on the bg. No purple, no neon.
const PALETTE = [
  { bg: '#063F43', fg: '#F4DDC3' }, // accent (deep teal) → warm sand
  { bg: '#102B2F', fg: '#E8B89A' }, // ink teal → terracotta wash
  { bg: '#C65F3F', fg: '#F8EFE0' }, // terracotta → ivory
  { bg: '#7A4F30', fg: '#F4DDC3' }, // copper → warm sand
  { bg: '#6C8D6E', fg: '#0F1F1B' }, // sage → ink
  { bg: '#3F4D8A', fg: '#F4EDDC' }, // indigo → cream
  { bg: '#C2342A', fg: '#FFFDF8' }, // vermillion → card white
  { bg: '#1F1A14', fg: '#D9A661' }, // black-brown → gold
]

// Center motif options. Each takes (px, fg) and returns SVG children.
const MOTIFS = {
  none: () => null,
  ring: (px, fg) => (
    <circle
      cx={px / 2} cy={px / 2}
      r={px * 0.13}
      fill="none" stroke={fg} strokeWidth={px * 0.04}
    />
  ),
  dot: (px, fg) => (
    <circle cx={px / 2} cy={px / 2} r={px * 0.08} fill={fg} />
  ),
  diamond: (px, fg) => {
    const s = px * 0.17
    const c = px / 2
    return (
      <polygon points={`${c},${c - s} ${c + s},${c} ${c},${c + s} ${c - s},${c}`} fill={fg} />
    )
  },
  plus: (px, fg) => {
    const t = px * 0.05
    const l = px * 0.18
    const c = px / 2
    return (
      <g fill={fg}>
        <rect x={c - t / 2} y={c - l} width={t} height={l * 2} />
        <rect x={c - l}     y={c - t / 2} width={l * 2} height={t} />
      </g>
    )
  },
}

const MOTIF_KEYS = ['none', 'ring', 'dot', 'diamond', 'plus']

const SIZES = { xs: 20, sm: 32, md: 52, lg: 96, xl: 128 }
const RADII = { xs: 5,  sm: 8,  md: 12, lg: 18, xl: 22  }

// Grid is 5×5; only columns 0..2 are randomly filled, columns 3..4 mirror.
// This is the same trick GitHub identicons use to keep every sigil balanced.
const COLS = 5
const ROWS = 5

function buildPattern(rand) {
  // Filledness — bias slightly toward sparse so the canvas reads architectural,
  // not crowded. Threshold 0.45 → roughly 45% of left half filled.
  const threshold = 0.40 + rand() * 0.15
  const grid = []
  for (let r = 0; r < ROWS; r++) {
    const row = []
    // left half (cols 0..2 inclusive — col 2 is the spine, single column)
    for (let c = 0; c < 3; c++) row.push(rand() < threshold)
    // mirror cols 1, 0 → cols 3, 4
    row.push(row[1])
    row.push(row[0])
    grid.push(row)
  }
  return grid
}

export default function AgentSigil({ agentId, size = 'md', className, style }) {
  const px = SIZES[size] ?? SIZES.md
  const rx = RADII[size] ?? RADII.md
  const seed = djb2(String(agentId ?? 'default'))
  const rand = mulberry32(seed)

  // Pick palette + motif first so they're stable
  const palette = PALETTE[Math.floor(rand() * PALETTE.length)]
  const motifKey = MOTIF_KEYS[Math.floor(rand() * MOTIF_KEYS.length)]

  // For tiny renders, the 5×5 grid becomes noise — render a simplified version:
  // solid bg + center motif scaled up. Keeps avatars legible at 20–32 px.
  if (size === 'xs' || size === 'sm') {
    const motif = MOTIFS[motifKey === 'none' ? 'dot' : motifKey](px, palette.fg)
    return (
      <svg
        width={px} height={px} viewBox={`0 0 ${px} ${px}`}
        aria-hidden="true"
        className={className}
        style={{ display: 'block', flexShrink: 0, borderRadius: rx, ...style }}
      >
        <rect width={px} height={px} rx={rx} fill={palette.bg} />
        <g style={{ transform: 'scale(1.6)', transformOrigin: 'center', transformBox: 'fill-box' }}>
          {motif}
        </g>
      </svg>
    )
  }

  const grid = buildPattern(rand)
  const cell = px / COLS
  // Tiny inset on each cell so blocks read as discrete tiles, not a wall.
  const gap = Math.max(0.5, cell * 0.04)

  const cells = []
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      if (!grid[r][c]) continue
      cells.push(
        <rect
          key={`${r}-${c}`}
          x={c * cell + gap}
          y={r * cell + gap}
          width={cell - gap * 2}
          height={cell - gap * 2}
          fill={palette.fg}
        />
      )
    }
  }

  const motif = MOTIFS[motifKey](px, palette.fg)
  const clipId = `as-${seed}-${size}`

  return (
    <svg
      width={px} height={px} viewBox={`0 0 ${px} ${px}`}
      aria-hidden="true"
      className={className}
      style={{ display: 'block', flexShrink: 0, borderRadius: rx, ...style }}
    >
      <defs>
        <clipPath id={clipId}>
          <rect x="0" y="0" width={px} height={px} rx={rx} />
        </clipPath>
      </defs>
      <g clipPath={`url(#${clipId})`}>
        <rect width={px} height={px} fill={palette.bg} />
        {cells}
        {motif}
      </g>
      <rect
        x="0.5" y="0.5"
        width={px - 1} height={px - 1}
        rx={rx}
        fill="none"
        stroke="rgba(0, 0, 0, 0.08)"
        strokeWidth="1"
      />
    </svg>
  )
}
