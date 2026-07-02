"""Mutable state object threaded through the autopilot-tick pipeline stages.

Sibling of `app.services.pipeline.state.PipelineState` — same philosophy
(Pattern #5): a single growing dataclass written by stages and read by
`_run_tick` when it projects the final value into `AutopilotTickResponse`.

Field set is narrow on purpose — only values that at least one stage
reads or writes. The autopilot tick touches several independent sessions
and side-effects; state carries just the identifiers and payloads the
stages thread between themselves, not every intermediate.
"""

from dataclasses import dataclass, field
from typing import Any

from app.models import AutopilotConfig
from app.services.gap_detector import GapTarget, ScoredGap


@dataclass
class AutopilotState:
    """Intermediate per-tick state written by stages, read by `_run_tick`.

    Life cycle:
      - GapDetectionStage:     writes gap, scored_gap, focus_label, focus_context,
                               recent_queries, gap_source, gap_target_desc.
                               Raises ShortCircuit(state) if policy dictates a
                               no-op tick (rare — kept for parity + extensibility).
      - QueryGenerationStage:  writes generated_query, appends to total_cost.
      - PipelineExecutionStage:writes query_id, neurons_activated; appends cost.
      - EvaluationStage:       writes eval_overall, eval_text; appends cost.
      - RefinementStage:       writes reasoning, updates, new_neurons; appends cost.
      - ProposalCurationStage: writes assembled_prompt, proposal_id.
      - PersistenceStage:      writes run_id (AutopilotRun row).
    """

    # --- inputs (set by caller before the first stage) ---
    config: AutopilotConfig

    # --- detect stage ---
    gap: GapTarget | None = None
    scored_gap: ScoredGap | None = None
    focus_label: str | None = None
    focus_context: str = ""
    recent_queries: list[str] = field(default_factory=list)
    gap_source: str = "directive"
    gap_target_desc: str = ""

    # --- generate stage ---
    generated_query: str = "(generation failed)"

    # --- execute stage ---
    query_id: int | None = None
    neurons_activated: int = 0

    # --- evaluate stage ---
    eval_overall: int = 0
    eval_text: str = ""

    # --- refine stage ---
    refine_reasoning: str = ""
    updates: list[dict] = field(default_factory=list)
    new_neurons: list[dict] = field(default_factory=list)

    # --- proposal curation stage ---
    assembled_prompt: str | None = None
    proposal_id: int | None = None
    # Write-gate routing outcome: "auto" (approved + applied by policy) or
    # "queue" (awaiting human review)
    gate_route: str = "queue"

    # --- persistence stage ---
    run_id: int | None = None

    # --- aggregated across stages ---
    total_cost: float = 0.0

    def run_fields(self) -> dict[str, Any]:
        """Kwargs projected into `AutopilotRun.__init__` on persistence."""
        return {
            "directive": self.config.directive,
            "focus_neuron_label": self.focus_label,
            "gap_source": self.gap_source,
            "gap_target": self.gap_target_desc,
            "query_id": self.query_id,
            "neurons_activated": self.neurons_activated,
            "updates_applied": 0,
            "neurons_created": 0,
            "eval_overall": self.eval_overall,
            "eval_text": self.eval_text or None,
            "refine_reasoning": self.refine_reasoning or None,
            "proposal_id": self.proposal_id,
        }
