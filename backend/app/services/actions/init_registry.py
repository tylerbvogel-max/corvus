"""Register all action handlers at app startup.

Called once from app.main:lifespan. Adding a new action kind = import its
module here and call register_action.
"""

from app.services.action_bus import register_action
from app.services.actions.eval_score_set import (
    EvalScoreSetInput,
    handle_eval_score_set,
)
from app.services.actions.neuron_create import (
    NeuronCreateInput,
    handle_neuron_create,
)
from app.services.actions.neuron_refine import (
    NeuronRefineInput,
    handle_neuron_refine,
)
from app.services.actions.proposal_apply import (
    ProposalApplyInput,
    handle_proposal_apply,
)
from app.services.actions.edge_rescale import (
    EdgeRescaleInput,
    handle_edge_rescale,
)
from app.services.actions.edge_link import (
    EdgeLinkInput,
    handle_edge_link,
)
from app.services.actions.output_policy_check import (
    OutputPolicyCheckInput,
    handle_output_policy_check,
)
from app.services.actions.eval_run_lifecycle import (
    EvalRunCompleteInput,
    EvalRunStartInput,
    handle_eval_run_complete,
    handle_eval_run_start,
)
from app.services.actions.edge_rewire import (
    EdgeRewireInput,
    handle_edge_rewire,
)
from app.services.actions.neuron_stats_rebuild import (
    NeuronStatsRebuildInput,
    handle_neuron_stats_rebuild,
)
from app.services.actions.proposal_reconsolidate import (
    ProposalReconsolidateInput,
    handle_proposal_reconsolidate,
)


def init_actions_registry() -> None:
    """Register every action kind known to Corvus."""
    register_action(
        kind="eval.score.set",
        schema=EvalScoreSetInput,
        handler=handle_eval_score_set,
        requires_approval=False,
    )
    register_action(
        kind="proposal.apply",
        schema=ProposalApplyInput,
        handler=handle_proposal_apply,
        requires_approval=False,
    )
    register_action(
        kind="neuron.create",
        schema=NeuronCreateInput,
        handler=handle_neuron_create,
        requires_approval=False,
    )
    register_action(
        kind="neuron.refine",
        schema=NeuronRefineInput,
        handler=handle_neuron_refine,
        requires_approval=False,
    )
    register_action(
        kind="edge.rescale",
        schema=EdgeRescaleInput,
        handler=handle_edge_rescale,
        requires_approval=False,
    )
    register_action(
        kind="edge.link",
        schema=EdgeLinkInput,
        handler=handle_edge_link,
        requires_approval=False,
    )
    register_action(
        kind="output.policy.check",
        schema=OutputPolicyCheckInput,
        handler=handle_output_policy_check,
        requires_approval=False,
    )
    register_action(
        kind="eval.run.start",
        schema=EvalRunStartInput,
        handler=handle_eval_run_start,
        requires_approval=False,
    )
    register_action(
        kind="eval.run.complete",
        schema=EvalRunCompleteInput,
        handler=handle_eval_run_complete,
        requires_approval=False,
    )
    register_action(
        kind="edge.rewire",
        schema=EdgeRewireInput,
        handler=handle_edge_rewire,
        requires_approval=False,
    )
    register_action(
        kind="neuron.stats.rebuild",
        schema=NeuronStatsRebuildInput,
        handler=handle_neuron_stats_rebuild,
        requires_approval=False,
    )
    register_action(
        kind="proposal.reconsolidate",
        schema=ProposalReconsolidateInput,
        handler=handle_proposal_reconsolidate,
        requires_approval=False,
    )
