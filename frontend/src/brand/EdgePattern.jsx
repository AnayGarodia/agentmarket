import './EdgePattern.css'

export default function EdgePattern({ className = '', side = 'top' }) {
  return <div className={`edge-pattern edge-pattern--${side} ${className}`.trim()} aria-hidden="true" />
}
