import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// Mock the API module so the page never tries to hit a real fetch and we can
// drive its loaded/error/empty states deterministically.
vi.mock('../api', () => ({
  fetchRecipes: vi.fn(),
  runRecipe: vi.fn(),
}))

// Mock the auth context so the page has an apiKey to trigger the effect.
vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({ apiKey: 'test-key' }),
}))

// Mock react-router so the page can call useNavigate without a router wrap.
const navigateMock = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => navigateMock,
  }
})

// Topbar pulls in MarketContext; stub it so we don't have to mount providers
// for a unit-style test.
vi.mock('../layout/Topbar', () => ({
  default: ({ crumbs }) => <div data-testid="topbar">{crumbs?.[0]?.label}</div>,
}))

const { fetchRecipes, runRecipe } = await import('../api')
const { default: WorkflowsPage } = await import('./WorkflowsPage.jsx')

const TWO_RECIPES = [
  {
    slug: 'audit-deps',
    name: 'audit-deps',
    description: 'Audit a manifest for CVEs and licenses.',
    steps: [
      {
        node_id: 'audit',
        agent_id: 'aud-1',
        agent_slug: 'dependency_auditor',
        agent_name: 'dependency_auditor',
        role: 'primary',
        price_per_call_usd: 0.05,
      },
    ],
    default_input_schema: {
      type: 'object',
      properties: { manifest: { type: 'string' } },
      required: ['manifest'],
    },
    estimated_total_cost_usd: 0.05,
    missing_agents: [],
  },
  {
    slug: 'domain-health',
    name: 'domain-health',
    description: 'DNS / SSL / HTTP-header checks for one or more domains.',
    steps: [
      {
        node_id: 'inspect',
        agent_id: 'dns-1',
        agent_slug: 'dns_inspector',
        agent_name: 'dns_inspector',
        role: 'primary',
        price_per_call_usd: 0.10,
      },
    ],
    default_input_schema: {
      type: 'object',
      properties: { domains: { type: 'array', items: { type: 'string' } } },
      required: ['domains'],
    },
    estimated_total_cost_usd: 0.10,
    missing_agents: [],
  },
]

describe('WorkflowsPage', () => {
  beforeEach(() => {
    fetchRecipes.mockReset()
    runRecipe.mockReset()
    navigateMock.mockReset()
  })

  it('renders one card per recipe with name, slug, description, step pills, and cost', async () => {
    fetchRecipes.mockResolvedValue({ recipes: TWO_RECIPES, count: 2 })

    render(<WorkflowsPage />)

    // Both recipe names render. Use getAllByText to tolerate the slug echo
    // (slug === name for the two built-ins we mocked).
    await waitFor(() => {
      expect(screen.getAllByText('audit-deps').length).toBeGreaterThan(0)
      expect(screen.getAllByText('domain-health').length).toBeGreaterThan(0)
    })

    // Descriptions are visible.
    expect(screen.getByText(/Audit a manifest for CVEs/)).toBeInTheDocument()
    expect(screen.getByText(/DNS \/ SSL \/ HTTP-header checks/)).toBeInTheDocument()

    // Step agent slugs appear as pill labels.
    expect(screen.getByText('dependency_auditor')).toBeInTheDocument()
    expect(screen.getByText('dns_inspector')).toBeInTheDocument()

    // Costs render via fmtUsd ("$0.05" / "$0.10").
    expect(screen.getByText('$0.05')).toBeInTheDocument()
    expect(screen.getByText('$0.10')).toBeInTheDocument()
  })

  it('opens the run dialog pre-populated with the recipe input form when Run is clicked', async () => {
    fetchRecipes.mockResolvedValue({ recipes: TWO_RECIPES, count: 2 })

    render(<WorkflowsPage />)

    const runButtons = await screen.findAllByRole('button', { name: /run workflow/i })
    expect(runButtons).toHaveLength(2)

    fireEvent.click(runButtons[0])

    // Dialog opens with the recipe's name in the title.
    await waitFor(() => {
      expect(screen.getByText(/Run audit-deps/)).toBeInTheDocument()
    })

    // The form rendered the "manifest" field declared in the recipe schema.
    // Field label rendering varies by form internals; assert via the
    // accessible input name as a stable signal.
    const manifestField = await screen.findByLabelText(/manifest/i)
    expect(manifestField).toBeInTheDocument()
  })
})
