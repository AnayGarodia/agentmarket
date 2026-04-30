// Jaali — Indian stone screen geometry. Uses repeated octagram (8-point star)
// motifs cut from stone, with thin connecting lines. Reads as architectural
// rhythm, not decoration. SVG pattern lets us tile it seamlessly.

let _idCounter = 0
function uniqueId(prefix) {
  _idCounter += 1
  return `${prefix}-${_idCounter}`
}

// Repeating octagram lattice — for hero edges, large background panels.
export function JaaliLattice({ className = '', size = 56, opacity = 0.5, color = 'currentColor' }) {
  const id = uniqueId('jaali')
  return (
    <svg className={className} width="100%" height="100%" aria-hidden style={{ opacity }}>
      <defs>
        <pattern id={id} x="0" y="0" width={size} height={size} patternUnits="userSpaceOnUse">
          {/* Octagram (8-point star) at the center */}
          <g transform={`translate(${size / 2} ${size / 2})`} fill="none" stroke={color} strokeWidth="0.6">
            <rect x={-size * 0.32} y={-size * 0.32} width={size * 0.64} height={size * 0.64} />
            <rect
              x={-size * 0.32} y={-size * 0.32} width={size * 0.64} height={size * 0.64}
              transform="rotate(45)"
            />
            <circle cx="0" cy="0" r={size * 0.08} />
          </g>
          {/* Corner connectors */}
          <line x1="0" y1={size / 2} x2={size} y2={size / 2} stroke={color} strokeWidth="0.4" opacity="0.4" />
          <line x1={size / 2} y1="0" x2={size / 2} y2={size} stroke={color} strokeWidth="0.4" opacity="0.4" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill={`url(#${id})`} />
    </svg>
  )
}

// Vertical jaali column — Indian temple-screen style for page edges.
export function JaaliColumn({ className = '', rows = 8, color = 'var(--terracotta)' }) {
  return (
    <div className={className} aria-hidden>
      <svg width="64" height="100%" viewBox={`0 0 64 ${rows * 64}`} preserveAspectRatio="xMidYMid meet">
        {[...Array(rows)].map((_, r) => {
          const cy = r * 64 + 32
          return (
            <g key={r} transform={`translate(32 ${cy})`} fill="none" stroke={color} strokeWidth="0.8" opacity="0.45">
              {/* outer square */}
              <rect x="-22" y="-22" width="44" height="44" />
              {/* inner rotated square (octagram) */}
              <rect x="-22" y="-22" width="44" height="44" transform="rotate(45)" />
              {/* center dot */}
              <circle cx="0" cy="0" r="2.5" fill={color} stroke="none" opacity="0.7" />
              {/* connecting line down to next */}
              {r < rows - 1 && (
                <line x1="0" y1="22" x2="0" y2="42" stroke={color} strokeWidth="0.4" opacity="0.4" />
              )}
            </g>
          )
        })}
      </svg>
    </div>
  )
}

// Horizontal connecting band — paired arcs / rangoli rhythm.
export function JaaliBand({ className = '', count = 6, color = 'var(--copper)' }) {
  return (
    <svg className={className} width="100%" height="60" viewBox="0 0 600 60" preserveAspectRatio="xMidYMid meet" aria-hidden>
      {[...Array(count)].map((_, i) => {
        const cx = (i + 0.5) * (600 / count)
        return (
          <g key={i} transform={`translate(${cx} 30)`} fill="none" stroke={color} strokeWidth="0.7" opacity="0.4">
            <circle cx="0" cy="0" r="14" />
            <circle cx="0" cy="0" r="22" opacity="0.5" />
            <line x1="-30" y1="0" x2="-22" y2="0" />
            <line x1="22" y1="0" x2="30" y2="0" />
          </g>
        )
      })}
      {/* baseline */}
      <line x1="0" y1="30" x2="600" y2="30" stroke={color} strokeWidth="0.4" opacity="0.18" />
    </svg>
  )
}
