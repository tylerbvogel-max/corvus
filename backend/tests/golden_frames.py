"""Framed golden content shared by the reconsolidation test suites.

The NVM-incident golden packet is asserted from three test modules
(kernel, apply, fusionplan render) plus the throwaway replay script. When
mind-neuron-evidence-frame made construction syntax part of the contract,
four copies of the same prose became four places to forget to update — so
the canonical synthesis body lives here once.

These are the frames an honest reviewer produces for the NVM component:
same facts, same facet coverage, and the same no-invention property the
fingerprint checks enforce (every concrete signal appears in a member).
"""

# Canonical NVM-component synthesis. Context declares `reinforced` because
# every member asserts the same activation command and none contradicts it.
GOLDEN_NVM_CONTENT = (
    "Claim: NVM lives at ~/.config/nvm/nvm.sh. Explicitly "
    "`source ~/.config/nvm/nvm.sh && nvm use 22.22.0` before Node tooling "
    "for the corvus frontend, master-corvus, and Market-Analytics-Suite. "
    "This is a machine convention; repos may lack .nvmrc or engines "
    "enforcement.\n"
    "Entities: nvm, corvus frontend, master-corvus, Market-Analytics-Suite\n"
    "Time scope: stable-preference\n"
    "Context: reinforced — every member asserts the same activation command "
    "and none contradicts it.\n"
    "Evidence: each member records `source ~/.config/nvm/nvm.sh && nvm use "
    "22.22.0` before Node tooling.\n"
    "Future-use: a future agent running npm, npx, tsc, Vite or Playwright on "
    "this machine must activate the runtime first.\n"
    "Likely queries: Where is nvm installed on this machine?\n"
    "Confidence: high\n"
    "Volatility: stable"
)

# Two-member restatement: identical claims, so the fusion RETAINS canonical
# identity rather than composing a new one.
TWO_MEMBER_NVM_CONTENT = (
    "Claim: source ~/.config/nvm/nvm.sh && nvm use 22.22.0\n"
    "Entities: nvm\n"
    "Time scope: stable-preference\n"
    "Context: reinforced — both members state the same command.\n"
    "Evidence: members 1 and 2 both record this activation line.\n"
    "Future-use: needed before Node tooling runs.\n"
    "Likely queries: How do I activate nvm here?\n"
    "Confidence: high\n"
    "Volatility: stable"
)


def framed(claim: str, *, context: str, evidence: str, future_use: str,
           likely_queries: str, entities: str = "none",
           time_scope: str = "stable-preference", confidence: str = "medium",
           volatility: str = "stable") -> str:
    """Build a valid frame for ad-hoc test packets."""
    return (
        f"Claim: {claim}\nEntities: {entities}\nTime scope: {time_scope}\n"
        f"Context: {context}\nEvidence: {evidence}\n"
        f"Future-use: {future_use}\nLikely queries: {likely_queries}\n"
        f"Confidence: {confidence}\nVolatility: {volatility}"
    )
