"""Reference memory class — shared vocabulary + structural walls
(mind-reference-class).

A reference memory is document-ingested knowledge: the same SHAPE as a
learned lesson (label, content, embedding, entities, abstraction_type)
but different epistemic standing — asserted-by-a-source, not
lived-and-verified. It is NOT a parallel neuron type with its own
scoring/spread/dedup code paths; it is a class built from existing
metadata:

  - region  = "Library"  (RegionPolicy carries the scoring discount and
    the write-gate override)
  - source_origin = "document"  (+ NeuronSourceLink provenance rows)
  - node_type = "reference"  (keeps it out of every LESSON_TYPES flow)
  - authority_level capped at "informational"

THE IDENTITY WALL: anything that feeds the self-model — charter
promotion, the skill compiler, self-model growth — must filter with
reference_exclusion_filters(). The wall is deliberately redundant
(source_origin AND node_type AND region): a reference neuron mislabeled
on one axis still cannot promote. A PDF can become "what I can look
up", never "who I am".
"""

from sqlalchemy import or_

from app.models import Neuron

REFERENCE_REGION = "Library"
REFERENCE_NODE_TYPE = "reference"
REFERENCE_SOURCE_ORIGIN = "document"
# Set by an APPROVED earned-promotion proposal (human countersigned):
# the neuron leaves the reference class but keeps document provenance
# via its NeuronSourceLink rows. Revocation retains (and reports) these.
PROMOTED_SOURCE_ORIGIN = "document_promoted"
REFERENCE_AUTHORITY_CAP = "informational"
# Container node for a document's facts inside Library — navigational
# scaffolding, never a recall result (recall router excludes it).
DOCUMENT_NODE_TYPE = "document"


def is_reference(neuron: Neuron) -> bool:
    """True if a neuron belongs to the reference class on ANY axis."""
    assert neuron is not None, "is_reference requires a neuron"
    return (
        neuron.source_origin == REFERENCE_SOURCE_ORIGIN
        or neuron.node_type in (REFERENCE_NODE_TYPE, DOCUMENT_NODE_TYPE)
        or neuron.department == REFERENCE_REGION
    )


def reference_exclusion_filters() -> tuple:
    """SQLAlchemy conditions excluding reference-class neurons.

    Append these to every query that feeds the self-model (charter
    promotion, skill-compiler cluster load, self-model growth). Redundant
    on purpose: each condition alone is sufficient for a well-formed
    reference neuron; together they hold even for a mislabeled one.
    """
    filters = (
        Neuron.source_origin != REFERENCE_SOURCE_ORIGIN,
        Neuron.node_type.notin_((REFERENCE_NODE_TYPE, DOCUMENT_NODE_TYPE)),
        or_(Neuron.department.is_(None),
            Neuron.department != REFERENCE_REGION),
    )
    assert len(filters) == 3, "all three wall axes must be present"
    return filters
