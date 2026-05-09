"""First chunk of built-in agent specs (initial `specs = [...]` list)."""

from __future__ import annotations

from typing import Any

from core.models import (
    FinancialRequest,
)
from server.builtin_agents.constants import (
    BUILTIN_INTERNAL_ENDPOINTS as _BUILTIN_INTERNAL_ENDPOINTS,
)
from server.builtin_agents.constants import (
    CVELOOKUP_AGENT_ID as _CVELOOKUP_AGENT_ID,
)
from server.builtin_agents.constants import (
    FINANCIAL_AGENT_ID as _FINANCIAL_AGENT_ID,
)
from server.builtin_agents.constants import (
    QUALITY_JUDGE_AGENT_ID as _QUALITY_JUDGE_AGENT_ID,
)
from server.builtin_agents.schemas import output_schema_object as _output_schema_object
from server.builtin_agents.schemas import (
    quality_judge_input_schema as _quality_judge_input_schema,
)


def load_builtin_specs_part1() -> list[dict[str, Any]]:
    return [
        {
            "agent_id": _FINANCIAL_AGENT_ID,
            "name": "Financial Research Agent",
            "description": "Use when looking up financial data for a public company. Fetches the latest 10-K or 10-Q directly from SEC EDGAR, extracts concrete filing evidence, and returns a structured brief with grounded highlights, key risks, and a positive/neutral/negative signal tied to the filing.",
            "endpoint_url": _BUILTIN_INTERNAL_ENDPOINTS[_FINANCIAL_AGENT_ID],
            "price_per_call_usd": 0.03,
            "tags": ["financial-research", "sec-filings", "equity-analysis"],
            "input_schema": FinancialRequest.model_json_schema(),
            "output_schema": _output_schema_object(
                {
                    "ticker": {"type": "string"},
                    "company_name": {"type": "string"},
                    "filing_type": {"type": "string"},
                    "filing_date": {"type": "string"},
                    "business_summary": {"type": "string"},
                    "recent_financial_highlights": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "key_risks": {"type": "array", "items": {"type": "string"}},
                    "signal": {"type": "string"},
                    "signal_reasoning": {"type": "string"},
                    "generated_at": {"type": "string"},
                },
                required=["ticker", "signal"],
            ),
            "output_examples": [
                {
                    "input": {"ticker": "AAPL"},
                    "output": {
                        "ticker": "AAPL",
                        "company_name": "Apple Inc.",
                        "filing_type": "10-Q",
                        "filing_date": "2026-01-31",
                        "business_summary": "Consumer hardware and services ecosystem.",
                        "recent_financial_highlights": [
                            "Revenue growth in Services",
                            "Stable gross margin",
                        ],
                        "key_risks": [
                            "Regulatory pressure",
                            "Supply chain concentration",
                        ],
                        "signal": "positive",
                        "signal_reasoning": "Recurring revenue expansion offsets hardware cyclicality.",
                        "generated_at": "2026-02-01T00:00:00+00:00",
                    },
                },
                {
                    "input": {"ticker": "TSLA"},
                    "output": {
                        "ticker": "TSLA",
                        "company_name": "Tesla, Inc.",
                        "filing_type": "10-Q",
                        "filing_date": "2026-02-05",
                        "business_summary": "EV manufacturing and energy storage business.",
                        "recent_financial_highlights": [
                            "Automotive margin compression",
                            "Energy growth",
                        ],
                        "key_risks": [
                            "Price competition",
                            "Execution risk on new models",
                        ],
                        "signal": "neutral",
                        "signal_reasoning": "Growth opportunities remain, but profitability volatility is elevated.",
                        "generated_at": "2026-02-06T00:00:00+00:00",
                    },
                },
            ],
        },
        {
            "agent_id": _QUALITY_JUDGE_AGENT_ID,
            "name": "Quality Judge Agent",
            "description": "Internal verification worker that scores completed outputs before settlement.",
            "endpoint_url": _BUILTIN_INTERNAL_ENDPOINTS[_QUALITY_JUDGE_AGENT_ID],
            "price_per_call_usd": 0.01,
            "tags": ["quality", "internal"],
            "input_schema": _quality_judge_input_schema(),
            "output_schema": _output_schema_object(
                {
                    "verdict": {"type": "string"},
                    "score": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                required=["verdict", "score", "reason"],
            ),
            "output_examples": [
                {
                    "input": {
                        "input_payload": {"task": "Summarize filing risks"},
                        "output_payload": {
                            "summary": "Identified debt covenant and supply-chain risks."
                        },
                        "agent_description": "SEC filing analyst",
                    },
                    "output": {
                        "verdict": "pass",
                        "score": 86,
                        "reason": "Output is relevant, structured, and addresses requested risk focus.",
                    },
                },
                {
                    "input": {
                        "input_payload": {"task": "Provide concise bug report"},
                        "output_payload": {"text": "Looks good."},
                        "agent_description": "Code review specialist",
                    },
                    "output": {
                        "verdict": "fail",
                        "score": 22,
                        "reason": "Response is too generic and lacks actionable findings.",
                    },
                },
            ],
            "internal_only": True,
        },
        {
            "agent_id": _CVELOOKUP_AGENT_ID,
            "name": "CVE Lookup Agent",
            "description": "Use when the user wants live CVE data for a package or specific CVE ID. Queries OSV.dev for ecosystem-aware package lookups (npm, PyPI) and NIST NVD for direct CVE-ID lookups — not LLM memory. Returns CVSS score, exploit availability, affected version range, and recommended fix for each CVE. VARIABLE BILLING: $0.01 for 1 CVE ID, $0.03 for up to 5 CVE IDs, $0.06 for up to 10 CVE IDs (batch ID mode). Package scans are flat $0.01/call.",
            "endpoint_url": _BUILTIN_INTERNAL_ENDPOINTS[_CVELOOKUP_AGENT_ID],
            "price_per_call_usd": 0.01,
            "tags": ["security", "cve", "vulnerability-intel", "nvd", "packages"],
            "match_keywords": [
                "cve",
                "cves",
                "cvss",
                "nvd",
                "osv",
                "log4shell",
                "log4j",
                "shellshock",
                "heartbleed",
                "spring4shell",
                "exploit availability",
                "vulnerability intelligence",
                "vulnerability database",
                "patched in",
                "fixed in",
            ],
            "block_keywords": [
                "package.json vulnerabilities",
                "audit dependencies",
                "audit packages",
                "audit my dependencies",
                "audit my deps",
                "audit my project",
                "audit a python",
                "audit my python",
                "audit this manifest",
                "is this dependency",
                "is this package",
                "package safe",
                "package dangerous",
                "dependency safe",
                "dependency dangerous",
                "sbom",
                "software bill of materials",
                "owasp top 10",
                "owasp",
                "depndency",
                "dependancy",
            ],
            "category": "Security",
            "examples_sensitive": True,
            "input_schema": {
                "type": "object",
                "description": (
                    "Provide exactly one of cve_id, cve_ids, or packages. The agent will reject calls "
                    "that supply none of them or more than one."
                ),
                "properties": {
                    "cve_id": {
                        "type": "string",
                        "description": "A single CVE ID to look up directly (e.g. CVE-2021-44228). Use this for one-off lookups.",
                    },
                    "cve_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Multiple CVE IDs to look up (max 10). Use this for batch CVE-ID lookups.",
                    },
                    "packages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Array of package@version strings. Use this for ecosystem-aware vulnerability scanning.",
                        "example": ["express@4.17.1", "lodash@4.17.20"],
                    },
                    "ecosystem": {
                        "type": "string",
                        "enum": ["auto", "npm", "pypi"],
                        "default": "auto",
                        "description": "Ecosystem to query for package lookups. 'auto' (default) tries npm and PyPI based on the package name shape; pass 'npm' or 'pypi' to disambiguate when the same name exists in both registries (e.g. 'requests').",
                    },
                    "include_patched": {"type": "boolean", "default": False},
                },
                # Replaced jsonschema oneOf (whose error message reads as the
                # *opposite* of the actual problem when both fields are sent)
                # with a sentinel field that the agent runtime validates and
                # rejects with a clear error. This avoids the
                # "valid under each of {required:['cve_ids']}, {required:['cve_id']}"
                # garbled stack trace that confused buyers.
                "anyOf": [
                    {"required": ["cve_id"]},
                    {"required": ["cve_ids"]},
                    {"required": ["packages"]},
                ],
            },
            "output_schema": {
                "type": "object",
                "properties": {
                    "results": {"type": "array", "items": {"type": "object"}},
                    "billing_units_actual": {
                        "type": "integer",
                        "description": "Number of successful CVE lookups (for per-CVE billing in direct ID mode)",
                    },
                    "total_vulnerable": {"type": "integer"},
                    "summary": {"type": "string"},
                },
            },
            "variable_pricing": {
                "model": "tiered",
                "field": "cve_ids",
                "field_type": "array",
                "unit_label": "CVE",
                "tiers": [
                    {"max_units": 1, "price_usd": 0.01},
                    {"max_units": 5, "price_usd": 0.03},
                    {"max_units": 10, "price_usd": 0.06},
                ],
            },
            "output_examples": [
                {
                    "input": {"packages": ["lodash@4.17.20", "express@4.17.1"]},
                    "output": {
                        "results": [
                            {
                                "package": "lodash",
                                "version": "4.17.20",
                                "cve": "CVE-2019-10744",
                                "cvss": 9.1,
                                "severity": "critical",
                            }
                        ],
                        "total_vulnerable": 2,
                        "total_packages_checked": 2,
                        "summary": "lodash@4.17.20 has 2 known CVEs including CVE-2019-10744 (prototype pollution, CVSS 9.1).",
                    },
                },
            ],
        },
    ]
