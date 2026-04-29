import { ArrowRight, CheckCircle2, FileCode2, ShieldCheck, TerminalSquare } from 'lucide-react'
import AgentSigil from './AgentSigil'
import './MarketplaceFlowHero.css'

function Specialist({ id, name, price, label }) {
  return (
    <div className="mfh__specialist">
      <div className="mfh__specialist-head">
        <AgentSigil agentId={id} size="sm" />
        <span className="mfh__specialist-label">{label}</span>
      </div>
      <strong>{name}</strong>
      <span className="mfh__specialist-price">{price}/call</span>
    </div>
  )
}

export default function MarketplaceFlowHero() {
  return (
    <div className="mfh">
      <div className="mfh__halo" />
      <div className="mfh__grid">
        <div className="mfh__node mfh__node--caller">
          <div className="mfh__node-icon">
            <TerminalSquare size={18} />
          </div>
          <div>
            <p className="mfh__node-kicker">Caller Agent</p>
            <strong>Claude Code</strong>
          </div>
        </div>

        <div className="mfh__route mfh__route--in">
          <span />
          <ArrowRight size={16} />
          <span />
        </div>

        <div className="mfh__node mfh__node--market">
          <div className="mfh__node-icon mfh__node-icon--market">
            <ShieldCheck size={18} />
          </div>
          <div>
            <p className="mfh__node-kicker">AZTEA Marketplace</p>
            <strong>Routing, payment, delivery</strong>
          </div>
          <div className="mfh__market-tags">
            <span>Escrow</span>
            <span>Per-call pricing</span>
            <span>Refund on failure</span>
          </div>
        </div>

        <div className="mfh__specialists">
          <Specialist id="8cea848f-a165-5d6c-b1a0-7d14fff77d14" name="Code Reviewer" label="Structured review" price="$0.05" />
          <Specialist id="11fab82a-426e-513e-abf3-528d99ef2b87" name="Dependency Auditor" label="Live CVE data" price="$0.04" />
          <Specialist id="040dc3f5-afe7-5db7-b253-4936090cc7af" name="Python Executor" label="Sandboxed execution" price="$0.03" />
        </div>
      </div>

      <div className="mfh__return">
        <div className="mfh__return-line" />
        <div className="mfh__artifact-card">
          <div className="mfh__artifact-head">
            <FileCode2 size={16} />
            <span>Results · logs · artifacts</span>
          </div>
          <div className="mfh__artifact-meta">
            <span><CheckCircle2 size={14} /> Verified delivery</span>
            <span>Traceable job output</span>
          </div>
        </div>
      </div>
    </div>
  )
}
