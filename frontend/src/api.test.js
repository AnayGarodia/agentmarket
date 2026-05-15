import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fetchRecipes, runRecipe } from './api.js'

const ORIGINAL_FETCH = globalThis.fetch

describe('fetchRecipes', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn()
  })
  afterEach(() => {
    globalThis.fetch = ORIGINAL_FETCH
  })

  it('GETs /recipes with the bearer header and returns the parsed body', async () => {
    const payload = { recipes: [{ slug: 'audit-deps', name: 'audit-deps' }], count: 1 }
    globalThis.fetch.mockResolvedValue({
      ok: true,
      status: 200,
      headers: {
        get: (name) => (name.toLowerCase() === 'content-type' ? 'application/json' : null),
      },
      json: async () => payload,
      text: async () => JSON.stringify(payload),
    })

    const body = await fetchRecipes('test-key')

    expect(globalThis.fetch).toHaveBeenCalledOnce()
    const [url, init] = globalThis.fetch.mock.calls[0]
    // The base prefix is determined at module load time; we only assert the
    // suffix so this test stays stable under different VITE_API_BASE_URL
    // configurations (dev, CI, prod).
    expect(String(url)).toMatch(/\/recipes$/)
    expect(init.method ?? 'GET').toBe('GET')
    expect(init.headers.Authorization).toBe('Bearer test-key')
    expect(body).toEqual(payload)
  })

  it('runRecipe POSTs to /recipes/{slug}/run with the input_payload envelope', async () => {
    const payload = { run_id: 'run_xyz', recipe_id: 'audit-deps', status: 'running' }
    globalThis.fetch.mockResolvedValue({
      ok: true,
      status: 200,
      headers: {
        get: (name) => (name.toLowerCase() === 'content-type' ? 'application/json' : null),
      },
      json: async () => payload,
      text: async () => JSON.stringify(payload),
    })

    const body = await runRecipe('test-key', 'audit-deps', { manifest: 'requirements.txt' })

    expect(globalThis.fetch).toHaveBeenCalledOnce()
    const [url, init] = globalThis.fetch.mock.calls[0]
    expect(String(url)).toMatch(/\/recipes\/audit-deps\/run$/)
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ input_payload: { manifest: 'requirements.txt' } })
    expect(init.headers.Authorization).toBe('Bearer test-key')
    expect(body).toEqual(payload)
  })
})
