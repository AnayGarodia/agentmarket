import { useState } from 'react'
import { TerminalSquare, ShieldCheck, Code2, Package, Zap, FileCode2 } from 'lucide-react'
import './MarketplacePanel.css'

const SPECIALISTS = [
  { id: 'review', icon: Code2,        name: 'Code Reviewer',      price: '$0.05' },
  { id: 'audit',  icon: Package,      name: 'Dependency Auditor', price: '$0.04' },
  { id: 'exec',   icon: Zap,          name: 'Python Executor',    price: '$0.03' },
]

// Architectural marketplace panel: caller agent → Aztea routing node (with
// jaali/rangoli geometry) → three specialist listings, with a sage return path
// at the bottom. One dominant idea, restrained line language, no spaghetti.
export default function MarketplacePanel() {
  const [active, setActive] = useState(null)

  return (
    <div className="mp">
      {/* Architectural arched frame */}
      <svg className="mp__frame" viewBox="0 0 600 520" preserveAspectRatio="none" aria-hidden>
        <path
          d="M 20 500 L 20 140 Q 20 20 300 20 Q 580 20 580 140 L 580 500"
          fill="none"
          stroke="var(--border)"
          strokeWidth="1"
        />
      </svg>

      {/* Faint rangoli geometry behind central node */}
      <svg className="mp__rangoli" viewBox="-100 -100 200 200" aria-hidden>
        {[...Array(12)].map((_, i) => (
          <line
            key={i}
            x1="0" y1="0"
            x2={Math.cos((i * Math.PI) / 6) * 90}
            y2={Math.sin((i * Math.PI) / 6) * 90}
            stroke="var(--copper)"
            strokeWidth="0.4"
            opacity="0.35"
          />
        ))}
        <circle cx="0" cy="0" r="40" fill="none" stroke="var(--copper)" strokeWidth="0.5" opacity="0.4" />
        <circle cx="0" cy="0" r="62" fill="none" stroke="var(--copper)" strokeWidth="0.4" opacity="0.3" />
        <circle cx="0" cy="0" r="84" fill="none" stroke="var(--copper)" strokeWidth="0.3" opacity="0.22" />
        <g transform="rotate(45)">
          <rect x="-50" y="-50" width="100" height="100" fill="none" stroke="var(--copper)" strokeWidth="0.4" opacity="0.3" />
        </g>
        <rect x="-50" y="-50" width="100" height="100" fill="none" stroke="var(--copper)" strokeWidth="0.4" opacity="0.3" />
      </svg>

      <div className="mp__grid">
        {/* Caller — left */}
        <div className="mp__caller">
          <div className="mp__node mp__node--caller">
            <div className="mp__node-icon"><TerminalSquare size={16} strokeWidth={1.6} /></div>
            <div className="mp__node-text">
              <span className="mp__node-kicker">Caller agent</span>
              <strong>Claude Code</strong>
            </div>
          </div>
        </div>

        {/* Outbound route */}
        <svg className="mp__route mp__route--out" viewBox="0 0 100 4" preserveAspectRatio="none" aria-hidden>
          <line x1="0" y1="2" x2="100" y2="2"
            stroke={active != null ? 'var(--terracotta)' : 'var(--border-bright)'}
            strokeWidth="1"
          />
          <polygon
            points="98,0.5 100,2 98,3.5"
            fill={active != null ? 'var(--terracotta)' : 'var(--border-bright)'}
          />
        </svg>

        {/* Aztea center node */}
        <div className="mp__hub">
          <div className="mp__hub-inner">
            <div className="mp__hub-icon"><ShieldCheck size={18} strokeWidth={1.6} /></div>
            <span className="mp__hub-label">AZTEA</span>
            <span className="mp__hub-sub">Marketplace</span>
          </div>
        </div>

        {/* Specialists — right */}
        <div className="mp__specialists">
          {SPECIALISTS.map((s, i) => {
            const Icon = s.icon
            const isHot = active === s.id
            return (
              <div
                key={s.id}
                className={`mp__spec-row ${isHot ? 'is-active' : ''}`}
                onMouseEnter={() => setActive(s.id)}
                onMouseLeave={() => setActive(null)}
                style={{ '--row-i': i }}
              >
                <svg className="mp__spec-line" viewBox="0 0 60 4" preserveAspectRatio="none" aria-hidden>
                  <line
                    x1="0" y1="2" x2="60" y2="2"
                    stroke={isHot ? 'var(--terracotta)' : 'var(--border-bright)'}
                    strokeWidth="1"
                  />
                </svg>
                <div className="mp__spec-card">
                  <div className="mp__spec-icon"><Icon size={14} strokeWidth={1.6} /></div>
                  <div className="mp__spec-text">
                    <strong>{s.name}</strong>
                    <span>{s.price} / call</span>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Return route */}
      <div className="mp__return">
        <svg className="mp__return-line" viewBox="0 0 600 60" preserveAspectRatio="none" aria-hidden>
          <path
            d="M 540 8 Q 560 8 560 32 L 560 44 Q 560 52 548 52 L 52 52 Q 40 52 40 44 L 40 32 Q 40 8 60 8"
            fill="none"
            stroke="var(--sage)"
            strokeWidth="1"
            opacity="0.7"
          />
        </svg>
        <div className="mp__return-card">
          <FileCode2 size={13} strokeWidth={1.7} />
          <span>Results · logs · artifacts</span>
        </div>
      </div>
    </div>
  )
}
