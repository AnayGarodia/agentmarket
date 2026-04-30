import { useEffect } from 'react'
import { X } from 'lucide-react'
import AuthPanel from './AuthPanel'
import './AuthDialog.css'

// Modal wrapper around AuthPanel. Triggered from the landing page when an
// unauthenticated user clicks Sign in / Get started / List skill.
export default function AuthDialog({ open, tab = 'signin', redirect, onClose }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') onClose?.() }
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = ''
      window.removeEventListener('keydown', onKey)
    }
  }, [open, onClose])

  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => {
      window.dispatchEvent(new CustomEvent('aztea:auth-tab', { detail: { tab, redirect } }))
    }, 50)
    return () => clearTimeout(t)
  }, [open, tab, redirect])

  if (!open) return null

  return (
    <div className="auth-dialog" role="dialog" aria-modal="true" aria-label="Sign in to Aztea">
      <button type="button" className="auth-dialog__bg" onClick={onClose} aria-label="Close" />
      <div className="auth-dialog__panel">
        <button type="button" className="auth-dialog__close" onClick={onClose} aria-label="Close dialog">
          <X size={16} />
        </button>
        <AuthPanel />
      </div>
    </div>
  )
}
