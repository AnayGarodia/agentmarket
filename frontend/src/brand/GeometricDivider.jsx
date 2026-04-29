import './GeometricDivider.css'

export default function GeometricDivider({ className = '' }) {
  return (
    <div className={`geo-divider ${className}`.trim()} aria-hidden="true">
      <span className="geo-divider__line" />
      <span className="geo-divider__motif">
        <span />
        <span />
        <span />
      </span>
      <span className="geo-divider__line" />
    </div>
  )
}
