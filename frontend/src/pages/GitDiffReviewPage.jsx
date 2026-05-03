// OWNS: the public-facing demo for the git-diff-review recipe.
// Users paste a unified diff and we run the platform pipeline (analyzer →
// LLM reviewer) and render the result as a copy-pasteable PR comment.
//
// INVARIANTS:
// - the page is reachable signed-in OR signed-out; a signed-out user is
//   prompted to sign in before running because the pipeline costs cents.
// - we NEVER ship the user's diff to anywhere other than the Aztea backend.

import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { motion } from 'motion/react'
import {
  ArrowRight, Copy, Check, Loader2, AlertCircle,
  GitPullRequest, Sparkles, Clock, Receipt,
} from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import { runPipeline, awaitPipelineRun } from '../api'
import AzteaMark from '../brand/AzteaMark'
import './GitDiffReviewPage.css'

const PIPELINE_ID = 'git-diff-review'

const SAMPLE_DIFF = `diff --git a/server/auth.py b/server/auth.py
index 7b2f9a..f3e4d5 100644
--- a/server/auth.py
+++ b/server/auth.py
@@ -42,9 +42,8 @@ def verify_session(token: str) -> dict | None:
     if not token:
         return None
-    decoded = jwt.decode(token, SECRET, algorithms=["HS256"])
-    if decoded.get("exp", 0) < time.time():
-        return None
+    decoded = jwt.decode(token, SECRET, options={"verify_signature": False})
+    # TODO: re-enable signature verification before next release
     return {"user_id": decoded["sub"], "email": decoded.get("email")}
`

export default function GitDiffReviewPage() {
  const { apiKey } = useAuth()
  const navigate = useNavigate()
  const [diff, setDiff] = useState('')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [elapsedMs, setElapsedMs] = useState(0)
  const elapsedRef = useRef(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!running) return
    const start = Date.now()
    const id = setInterval(() => setElapsedMs(Date.now() - start), 100)
    elapsedRef.current = id
    return () => clearInterval(id)
  }, [running])

  const onLoadSample = () => setDiff(SAMPLE_DIFF)

  const onRun = async () => {
    setError(null)
    setResult(null)
    if (!apiKey) {
      navigate(`/welcome?tab=signin&redirect=${encodeURIComponent('/demos/git-diff-review')}`)
      return
    }
    const trimmed = diff.trim()
    if (!trimmed) {
      setError('Paste a unified diff to review.')
      return
    }
    if (trimmed.length > 500_000) {
      setError('Diff is too large (max 500 KB). Trim to the changed files.')
      return
    }
    setRunning(true)
    setElapsedMs(0)
    try {
      const created = await runPipeline(apiKey, PIPELINE_ID, { diff: trimmed })
      const run = await awaitPipelineRun(apiKey, PIPELINE_ID, created.run_id, { intervalMs: 1500, timeoutMs: 180_000 })
      setResult(run)
    } catch (err) {
      setError(err?.message || 'Pipeline failed. Try again.')
    } finally {
      setRunning(false)
    }
  }

  const stages = useMemo(() => extractStages(result), [result])
  const review = stages.review
  const analyze = stages.analyze
  const totalCostCents = useMemo(() => sumCostCents(stages), [stages])
  const reviewMarkdown = useMemo(() => (review ? renderReviewMarkdown(review, analyze) : ''), [review, analyze])

  const onCopy = async () => {
    if (!reviewMarkdown) return
    try {
      await navigator.clipboard.writeText(reviewMarkdown)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setError('Could not copy to clipboard.')
    }
  }

  return (
    <div className="gdrp">
      <header className="gdrp__nav">
        <Link to="/" className="gdrp__brand"><AzteaMark size={22} /> <span>Aztea</span></Link>
        <Link to="/agents" className="gdrp__nav-link">All agents <ArrowRight size={14} /></Link>
      </header>

      <section className="gdrp__hero">
        <span className="gdrp__eyebrow"><Sparkles size={12} /> Pipeline demo</span>
        <h1 className="gdrp__h1">
          <span>Review a git diff with</span><br />
          <span className="gdrp__h1--accent">two agents in one call.</span>
        </h1>
        <p className="gdrp__lead">
          Stage 1 classifies risk deterministically (auth, money, removed tests, secrets).
          Stage 2 runs an LLM review using stage 1 as context, biased toward bugs and security.
          Output is a PR-ready comment.
        </p>
      </section>

      <section className="gdrp__body">
        <div className="gdrp__col gdrp__col--left">
          <div className="gdrp__panel">
            <div className="gdrp__panel-head">
              <h2 className="gdrp__panel-title">Your diff</h2>
              <button type="button" className="gdrp__link-btn" onClick={onLoadSample} disabled={running}>
                Try a sample
              </button>
            </div>
            <textarea
              className="gdrp__textarea"
              value={diff}
              onChange={(e) => setDiff(e.target.value)}
              placeholder="Paste a unified diff here. `git diff main` is a good starting point."
              spellCheck={false}
              disabled={running}
            />
            <div className="gdrp__panel-foot">
              <span className="gdrp__hint">{diff.length.toLocaleString()} chars</span>
              <button type="button" className="gdrp__btn gdrp__btn--primary" onClick={onRun} disabled={running}>
                {running ? (
                  <><Loader2 size={14} className="gdrp__spin" /> Reviewing… {(elapsedMs / 1000).toFixed(1)}s</>
                ) : apiKey ? (
                  <><GitPullRequest size={14} /> Run review</>
                ) : (
                  <>Sign in to run <ArrowRight size={14} /></>
                )}
              </button>
            </div>
          </div>

          {error && (
            <div className="gdrp__alert">
              <AlertCircle size={14} /> <span>{error}</span>
            </div>
          )}

          <div className="gdrp__pipeline">
            <h3 className="gdrp__panel-title gdrp__panel-title--small">Pipeline</h3>
            <ol className="gdrp__steps">
              <li className={`gdrp__step ${running && !analyze ? 'gdrp__step--active' : analyze ? 'gdrp__step--done' : ''}`}>
                <span className="gdrp__step-num">01</span>
                <div>
                  <span className="gdrp__step-title">analyze · git_diff_analyzer</span>
                  <span className="gdrp__step-sub">Risk classification — deterministic, no LLM</span>
                </div>
              </li>
              <li className={`gdrp__step ${running && analyze && !review ? 'gdrp__step--active' : review ? 'gdrp__step--done' : ''}`}>
                <span className="gdrp__step-num">02</span>
                <div>
                  <span className="gdrp__step-title">review · code_review_agent</span>
                  <span className="gdrp__step-sub">LLM review with stage 1 as context</span>
                </div>
              </li>
            </ol>
          </div>
        </div>

        <div className="gdrp__col gdrp__col--right">
          {!result && !running && (
            <div className="gdrp__placeholder">
              <span className="gdrp__placeholder-mark"><Sparkles size={20} /></span>
              <p>Result appears here. The output is a PR comment you can paste straight into GitHub.</p>
            </div>
          )}
          {running && !result && (
            <div className="gdrp__placeholder">
              <Loader2 size={20} className="gdrp__spin" />
              <p>Running pipeline… {(elapsedMs / 1000).toFixed(1)}s</p>
            </div>
          )}
          {result && (
            <div className="gdrp__panel">
              <div className="gdrp__panel-head">
                <h2 className="gdrp__panel-title">PR comment</h2>
                <div className="gdrp__panel-meta">
                  <span><Clock size={12} /> {result?.completed_at ? formatDuration(result) : '—'}</span>
                  {totalCostCents > 0 && (
                    <span><Receipt size={12} /> ${(totalCostCents / 100).toFixed(2)}</span>
                  )}
                  <button type="button" className="gdrp__icon-btn" onClick={onCopy}>
                    {copied ? <Check size={14} /> : <Copy size={14} />} {copied ? 'Copied' : 'Copy'}
                  </button>
                </div>
              </div>
              <pre className="gdrp__output">{reviewMarkdown}</pre>
            </div>
          )}
          {result && analyze && (
            <details className="gdrp__details">
              <summary>Stage 1 risk profile</summary>
              <pre>{JSON.stringify(analyze, null, 2)}</pre>
            </details>
          )}
        </div>
      </section>

      <footer className="gdrp__foot">
        <p className="gdrp__foot-text">
          Want to run this on every PR? <a href="https://github.com/AnayGarodia/aztea/blob/main/docs/recipes.md" target="_blank" rel="noreferrer">See the GitHub Action recipe →</a>
        </p>
      </footer>
    </div>
  )
}

// ── Helpers ──────────────────────────────────────────────────────────────

function extractStages(run) {
  if (!run || typeof run !== 'object') return {}
  const steps = run.steps || run.step_results || run.results
  if (!steps) return {}
  if (Array.isArray(steps)) {
    const map = {}
    for (const s of steps) {
      const id = s?.id || s?.node_id
      if (id) map[id] = s.output ?? s.result
    }
    return map
  }
  if (typeof steps === 'object') {
    const out = {}
    for (const [id, val] of Object.entries(steps)) {
      out[id] = val?.output ?? val
    }
    return out
  }
  return {}
}

function sumCostCents(stages) {
  return Object.values(stages || {}).reduce((acc, out) => {
    if (out && typeof out === 'object' && typeof out.cost_cents === 'number') return acc + out.cost_cents
    return acc
  }, 0)
}

function formatDuration(run) {
  const start = run?.started_at || run?.created_at
  const end = run?.completed_at || run?.updated_at
  if (!start || !end) return '—'
  const ms = new Date(end).getTime() - new Date(start).getTime()
  if (Number.isNaN(ms) || ms <= 0) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

function renderReviewMarkdown(review, analyze) {
  if (!review || typeof review !== 'object') return ''
  const lines = []
  const counts = review.severity_counts || {}
  const crit = Number(counts.critical || 0)
  const high = Number(counts.high || 0)
  if (crit) {
    lines.push(`<!-- aztea: critical -->`, `**❌ ${crit} critical issue${crit !== 1 ? 's' : ''} — block merge**`)
  } else if (high) {
    lines.push(`<!-- aztea: high -->`, `**⚠ ${high} high-severity issue${high !== 1 ? 's' : ''} — review before merge**`)
  } else if (typeof review.score === 'number' && review.score >= 90) {
    lines.push(`<!-- aztea: ok -->`, `**✅ Looks good — no blocking issues**`)
  } else {
    lines.push(`<!-- aztea: info -->`, `**Aztea review**`)
  }

  lines.push('', '## Code Review')
  if (typeof review.score === 'number') lines.push(`**Score:** ${review.score}/100`)
  if (review.summary) lines.push('', String(review.summary))
  const chip = Object.entries(counts)
    .filter(([, n]) => n)
    .map(([k, n]) => `${severityEmoji(k)} ${n} ${k}`)
    .join(' · ')
  if (chip) lines.push('', chip)

  const issues = Array.isArray(review.issues) ? review.issues.slice(0, 50) : []
  if (issues.length) {
    lines.push('', '### Issues')
    for (const issue of issues) {
      const sev = String(issue.severity || 'info')
      const cat = issue.category || ''
      const title = String(issue.title || issue.message || '').trim()
      const file = issue.file || issue.filename
      const line = issue.line
      const loc = file && line ? ` · \`${file}:${line}\`` : file ? ` · \`${file}\`` : ''
      const head = cat
        ? `- ${severityEmoji(sev)} **${sev}** _${cat}_${loc} — ${title}`
        : `- ${severityEmoji(sev)} **${sev}**${loc} — ${title}`
      lines.push(head)
      if (issue.suggestion) lines.push(`  - Fix: ${issue.suggestion}`)
    }
  }

  if (analyze && analyze.risk_summary) {
    const flags = Object.entries(analyze.risk_summary)
      .filter(([, v]) => (typeof v === 'boolean' ? v : Number(v) > 0))
      .map(([k, v]) => (typeof v === 'boolean' ? `⚠ ${k}` : `${k}: ${v}`))
    if (flags.length) {
      lines.push('', '---', '_Diff risk:_ ' + flags.join(' · '))
    }
  }
  return lines.join('\n')
}

function severityEmoji(sev) {
  return ({
    critical: '🔴',
    high:     '🟠',
    medium:   '🟡',
    low:      '🟢',
    info:     '🔵',
  })[String(sev).toLowerCase()] || '•'
}
