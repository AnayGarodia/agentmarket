import { useState, useMemo, useEffect, useRef } from 'react'
import Button from '../../ui/Button'
import Segmented from '../../ui/Segmented'
import { Zap, Radio, Lock, Unlock } from 'lucide-react'
import { validateInvokePayload } from '../../utils/inputGuards'
import './AgentInputForm.css'

const MODE_OPTIONS = [
  { value: 'sync',  label: 'Sync' },
  { value: 'async', label: 'Async' },
]

function estimateCost(variablePricing, payload) {
  if (!variablePricing) return null
  const { model, field, field_type, tiers, rate_usd, min_usd } = variablePricing
  const raw = payload?.[field]
  if (raw == null || raw === '') return null

  let units
  if (field_type === 'array') {
    units = Array.isArray(raw) ? raw.filter(Boolean).length
      : String(raw).split(/[\n,]+/).map(s => s.trim()).filter(Boolean).length
  } else {
    units = parseInt(raw, 10)
    if (isNaN(units) || units <= 0) return null
  }
  if (units <= 0) return null

  if (model === 'tiered') {
    const tier = tiers.find(t => units <= t.max_units) ?? tiers[tiers.length - 1]
    return { cost: tier.price_usd, units }
  } else if (model === 'per_unit') {
    return { cost: Math.max(min_usd ?? 0, units * (rate_usd ?? 0)), units }
  }
  return null
}

function deriveFields(schema) {
  if (Array.isArray(schema?.fields) && schema.fields.length > 0) return schema.fields
  if (!schema?.properties) return []
  const required = new Set(schema.required ?? [])
  return Object.entries(schema.properties).map(([name, def]) => {
    const label = name.charAt(0).toUpperCase() + name.slice(1).replace(/_/g, ' ')
    let type = 'text'
    if (def.enum) type = 'select'
    else if (def.type === 'boolean') type = 'checkbox'
    else if (def.type === 'integer' || def.type === 'number') type = 'number'
    else if (def.type === 'array') type = 'array'
    else if (['code', 'text', 'content', 'body', 'source'].includes(name)) type = 'textarea'
    return {
      name, label, type,
      options: def.enum,
      required: required.has(name),
      placeholder: def.example ?? def.examples?.[0] ?? '',
      hint: def.description,
      transform: ['ticker', 'symbol'].includes(name) ? 'uppercase' : undefined,
      default: def.default ?? (def.type === 'array' ? [] : def.type === 'boolean' ? false : ''),
      max_length: def.maxLength,
      min: def.minimum,
      max: def.maximum,
      schema_type: def.type,
    }
  })
}

// ---------------------------------------------------------------------------
// ArrayTagInput — proper tag-list for array fields
// ---------------------------------------------------------------------------

function ArrayTagInput({ id, value, onChange, placeholder, firstRef }) {
  const [draft, setDraft] = useState('')
  const items = Array.isArray(value) ? value : []

  const commit = (raw) => {
    const parts = raw.split(/[,\n]+/).map(s => s.trim()).filter(Boolean)
    if (!parts.length) return
    onChange([...items, ...parts])
    setDraft('')
  }

  const remove = (i) => onChange(items.filter((_, idx) => idx !== i))

  return (
    <div className="array-tag-input">
      {items.length > 0 && (
        <div className="array-tag-input__chips">
          {items.map((item, i) => (
            <span key={i} className="array-tag-input__chip">
              <span className="array-tag-input__chip-text">{item}</span>
              <button
                type="button"
                className="array-tag-input__chip-x"
                onClick={() => remove(i)}
                aria-label={`Remove ${item}`}
              >×</button>
            </span>
          ))}
        </div>
      )}
      <input
        id={id}
        ref={firstRef}
        type="text"
        className="aif__input"
        placeholder={items.length === 0
          ? (placeholder || 'Type a value and press Enter to add…')
          : 'Add another…'}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter') { e.preventDefault(); commit(draft) }
          if (e.key === 'Backspace' && !draft && items.length > 0) onChange(items.slice(0, -1))
        }}
        onBlur={() => { if (draft.trim()) commit(draft) }}
        onPaste={e => {
          const text = e.clipboardData.getData('text')
          if (text.includes(',') || text.includes('\n')) { e.preventDefault(); commit(text) }
        }}
      />
      {items.length > 0 && (
        <span className="aif__array-count">
          {items.length} item{items.length !== 1 ? 's' : ''} · press Enter to add more
        </span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main form
// ---------------------------------------------------------------------------

export default function AgentInputForm({ agent, onSubmit, loading, mode, onModeChange }) {
  const fields = useMemo(() => deriveFields(agent?.input_schema), [agent])
  const firstRef = useRef(null)

  const [values, setValues] = useState(() =>
    Object.fromEntries(fields.map(f => [
      f.name,
      f.default ?? (f.type === 'array' ? [] : f.type === 'checkbox' ? false : ''),
    ]))
  )
  const [privateTask, setPrivateTask] = useState(false)
  const [inputError, setInputError]   = useState('')
  const [fieldErrors, setFieldErrors] = useState({})

  useEffect(() => {
    setValues(Object.fromEntries(fields.map(f => [
      f.name,
      f.default ?? (f.type === 'array' ? [] : f.type === 'checkbox' ? false : ''),
    ])))
    setInputError('')
    setFieldErrors({})
  }, [agent?.agent_id]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const t = setTimeout(() => firstRef.current?.focus({ preventScroll: true }), 80)
    return () => clearTimeout(t)
  }, [agent?.agent_id])

  const set = (name, val) => {
    setValues(v => ({ ...v, [name]: val }))
    if (fieldErrors[name]) setFieldErrors(e => ({ ...e, [name]: null }))
  }

  const handleSubmit = (e) => {
    e.preventDefault()
    setInputError('')

    // Validate required fields and collect per-field errors
    const errs = {}
    for (const f of fields) {
      if (!f.required) continue
      const val = values[f.name]
      if (f.type === 'array') {
        if (!Array.isArray(val) || val.length === 0) {
          errs[f.name] = 'Add at least one item. Type a value and press Enter.'
        }
      } else if (!String(val ?? '').trim()) {
        errs[f.name] = 'This field is required.'
      }
    }
    if (Object.keys(errs).length > 0) {
      setFieldErrors(errs)
      // Focus the first errored field
      const firstErrName = fields.find(f => errs[f.name])?.name
      if (firstErrName) document.getElementById(`aif-${firstErrName}`)?.focus()
      return
    }

    const payload = {}
    fields.forEach(f => {
      let v = values[f.name]
      if (f.type === 'checkbox') {
        payload[f.name] = Boolean(v)
      } else if (f.type === 'number') {
        const n = f.schema_type === 'integer' ? parseInt(v, 10) : parseFloat(v)
        payload[f.name] = isNaN(n) ? v : n
      } else if (f.type === 'array') {
        payload[f.name] = Array.isArray(v) ? v
          : (typeof v === 'string' && v.trim()
            ? v.split(',').map(s => s.trim()).filter(Boolean)
            : [])
      } else {
        if (f.transform === 'uppercase') v = String(v ?? '').toUpperCase()
        payload[f.name] = v
      }
    })

    // Drop empty optional arrays — prevents oneOf "valid under each of" errors when a
    // field is present-but-empty (e.g. urls=[]) alongside a filled sibling (url="https://...")
    for (const f of fields) {
      if (f.type === 'array' && !f.required) {
        if (Array.isArray(payload[f.name]) && payload[f.name].length === 0) {
          delete payload[f.name]
        }
      }
    }

    // For oneOf schemas: keep only fields from the branch the user actually filled.
    const oneOf = agent?.input_schema?.oneOf
    if (Array.isArray(oneOf)) {
      const activeBranch = oneOf.find(branch =>
        (branch.required ?? []).every(key => {
          const val = payload[key]
          return Array.isArray(val) ? val.length > 0
            : (val !== undefined && val !== null && val !== '')
        })
      )
      if (activeBranch) {
        for (const branch of oneOf) {
          if (branch === activeBranch) continue
          for (const key of branch.required ?? []) {
            if (!(activeBranch.required ?? []).includes(key)) delete payload[key]
          }
        }
      }
    }

    const payloadError = validateInvokePayload(payload)
    if (payloadError) { setInputError(payloadError); return }

    onSubmit(payload, { privateTask })
  }

  const basePrice = `$${Number(agent?.price_per_call_usd ?? 0).toFixed(2)}`
  const estimatedCost = estimateCost(agent?.variable_pricing, values)
  const price = estimatedCost != null ? `$${Number(estimatedCost.cost).toFixed(2)}` : basePrice

  return (
    <form className="aif" onSubmit={handleSubmit} noValidate>
      {/* Fields */}
      <div className="aif__fields">
        {fields.length === 0 && (
          <p className="aif__no-schema">
            This agent has no defined input schema. Check its documentation.
          </p>
        )}

        {fields.map((f, i) => (
          <div key={f.name} className={`aif__field${fieldErrors[f.name] ? ' aif__field--error' : ''}`}>
            <label className="aif__label" htmlFor={`aif-${f.name}`}>
              {f.label ?? f.name}
              {f.required
                ? <span className="aif__required" title="Required">Required</span>
                : <span className="aif__optional">Optional</span>}
            </label>

            {f.hint && <p className="aif__hint">{f.hint}</p>}

            {f.type === 'textarea' ? (
              <textarea
                id={`aif-${f.name}`}
                ref={i === 0 ? firstRef : null}
                className="aif__textarea"
                placeholder={f.placeholder || 'Enter value…'}
                value={values[f.name]}
                onChange={e => set(f.name, e.target.value)}
                maxLength={f.max_length}
                rows={4}
              />
            ) : f.type === 'select' ? (
              <select
                id={`aif-${f.name}`}
                ref={i === 0 ? firstRef : null}
                className="aif__select"
                value={values[f.name]}
                onChange={e => set(f.name, e.target.value)}
              >
                {!(f.required) && <option value="">Choose...</option>}
                {(f.options ?? []).map(o => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : f.type === 'checkbox' ? (
              <label className="aif__checkbox-row">
                <input
                  id={`aif-${f.name}`}
                  ref={i === 0 ? firstRef : null}
                  type="checkbox"
                  checked={Boolean(values[f.name])}
                  onChange={e => set(f.name, e.target.checked)}
                />
                <span>Enable</span>
              </label>
            ) : f.type === 'number' ? (
              <input
                id={`aif-${f.name}`}
                ref={i === 0 ? firstRef : null}
                className="aif__input"
                type="number"
                placeholder={f.placeholder || String(f.default ?? '')}
                value={values[f.name]}
                onChange={e => set(f.name, e.target.value)}
                min={f.min}
                max={f.max}
                step={f.schema_type === 'integer' ? 1 : 'any'}
                autoComplete="off"
              />
            ) : f.type === 'array' ? (
              <ArrayTagInput
                id={`aif-${f.name}`}
                firstRef={i === 0 ? firstRef : null}
                value={values[f.name]}
                onChange={v => set(f.name, v)}
                placeholder={f.placeholder}
              />
            ) : (
              <input
                id={`aif-${f.name}`}
                ref={i === 0 ? firstRef : null}
                className="aif__input"
                type="text"
                placeholder={f.placeholder || 'Enter value…'}
                value={values[f.name]}
                onChange={e => set(f.name, e.target.value)}
                maxLength={f.max_length}
                autoComplete="off"
              />
            )}

            {fieldErrors[f.name] && (
              <p className="aif__field-error" role="alert">{fieldErrors[f.name]}</p>
            )}
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="aif__footer">
        <Segmented options={MODE_OPTIONS} value={mode} onChange={onModeChange} />
        <p className="aif__mode-help">
          {mode === 'async'
            ? 'Async queues a job you can monitor in Jobs.'
            : 'Sync returns output immediately in this panel.'}
        </p>

        {inputError && <p className="aif__error-banner" role="alert">{inputError}</p>}

        <div className="aif__price-bar">
          <span className="aif__price-label">
            {estimatedCost != null ? 'Estimated cost'
              : agent?.variable_pricing ? 'Price varies by usage'
              : 'Cost per call'}
          </span>
          <span className="aif__price-val">
            {price}
            {estimatedCost != null && agent?.variable_pricing?.unit_label && (
              <span className="aif__price-hint">
                {' '}for {estimatedCost.units} {agent.variable_pricing.unit_label}
                {estimatedCost.units !== 1 ? 's' : ''}
              </span>
            )}
          </span>
        </div>

        <button
          type="button"
          className={`aif__private-toggle${privateTask ? ' aif__private-toggle--on' : ''}`}
          onClick={() => setPrivateTask(p => !p)}
          title={privateTask
            ? 'Private: output will not be saved to work history'
            : 'Public: output may be saved as a work example'}
        >
          {privateTask ? <Lock size={11} /> : <Unlock size={11} />}
          {privateTask ? 'Private task' : 'Public task'}
        </button>

        <Button
          type="submit"
          variant="primary"
          size="md"
          loading={loading}
          className="aif__submit"
          icon={mode === 'async' ? <Radio size={14} /> : <Zap size={14} />}
        >
          {mode === 'async' ? `Create async job · ${price}` : `Run now · ${price}`}
        </Button>
      </div>
    </form>
  )
}
