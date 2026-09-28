"""Hooks for detecting and storing external references on neuron create/update."""

import json
from app.services.reference_detector import detect_neuron_references


def populate_external_references(neuron) -> None:
    """Detect external references in neuron content/summary and store as JSON.

    Call this after setting content/summary on a Neuron object, before commit.
    Mutates the neuron in place.
    """
    refs = detect_neuron_references(neuron.content, neuron.summary)
    neuron.external_references = json.dumps(refs) if refs else None
