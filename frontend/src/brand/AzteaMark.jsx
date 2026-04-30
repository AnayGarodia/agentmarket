// Aztea brand mark — a single 8-point star (octagram).
// Two squares overlapping at 45°, drawn as one continuous outline. One
// terracotta dot at the center. Bilateral + 8-fold rotational symmetry.
// Slow rotation via SMIL — same SVG works in the favicon.

export default function AzteaMark({ size = 24, className = '', animate = true }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="-32 -32 64 64"
      className={className}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <g>
        <rect x="-22" y="-22" width="44" height="44"
          stroke="currentColor" strokeWidth="2.4" fill="none" strokeLinejoin="round" />
        <rect x="-22" y="-22" width="44" height="44"
          stroke="currentColor" strokeWidth="2.4" fill="none" strokeLinejoin="round"
          transform="rotate(45)" />
        {animate && (
          <animateTransform
            attributeName="transform"
            type="rotate"
            from="0"
            to="360"
            dur="60s"
            repeatCount="indefinite"
          />
        )}
      </g>
      <circle cx="0" cy="0" r="3.5" fill="var(--terracotta, #C65F3F)" />
    </svg>
  )
}
