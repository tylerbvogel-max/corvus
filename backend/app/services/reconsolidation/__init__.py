"""Reconsolidation kernel (mind-reconsolidation-kernel).

Turns a judged duplicate component into exactly one faithful active
representation: component-coherent synthesis, field-specific signal
inheritance, deterministic graph rewiring, atomic proposal lifecycle.
Detection (mind_lint) nominates tissue; this package heals it.
"""

from app.services.reconsolidation.plan import (  # noqa: F401
    Disposition,
    Facet,
    FacetKind,
    FusionPlan,
    InheritancePreview,
    MemberSnapshot,
    RewiringPreview,
    snapshot_of,
)
from app.services.reconsolidation.validators import (  # noqa: F401
    PlanValidationError,
    assert_preflight,
    check_postconditions,
    preflight,
)
from app.services.reconsolidation.fingerprints import (  # noqa: F401
    fingerprint,
    nominates,
    signals_of_text,
)
from app.services.reconsolidation.inheritance import (  # noqa: F401
    build_inheritance_preview,
    dedup_events,
    derive_authority,
    derive_dates,
    embedding_input,
    embedding_sha256,
    member_provenance_gaps,
    replay_utility,
    rewiring_preview,
    union_distinct_queries,
)
from app.services.reconsolidation.review import (  # noqa: F401
    PacketValidationError,
    assemble_plan,
    compute_coverage_delta,
    decide_disposition,
    review_component,
    validate_packet,
)
from app.services.reconsolidation.rewiring import (  # noqa: F401
    EdgeDelete,
    EdgeUpsert,
    RewireOps,
    plan_rewire_ops,
)
from app.services.reconsolidation.apply import (  # noqa: F401
    ReconsolidationApplyError,
    parse_reconsolidation_spec,
    run_reconsolidation,
    synthesis_entities,
)
from app.services.reconsolidation.lifecycle import (  # noqa: F401
    ProposalStaleError,
    approve_and_apply,
    mark_superseded,
    reconsolidation_item_spec,
    revalidate_items,
    supersede_stale_approved,
)
