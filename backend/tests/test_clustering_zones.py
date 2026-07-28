from app.services.clustering import _build_cluster_record


def test_cluster_record_has_deterministic_name_and_representatives():
    neuron_info = {
        1: {"label": "NVM version activation for CLI", "department": "Environment"},
        2: {"label": "Node version selection with NVM", "department": "Projects"},
        3: {"label": "NVM CLI version troubleshooting", "department": "Harness"},
    }
    edges = [(1, 2, 0.9), (2, 3, 0.8), (1, 3, 0.7)]

    record = _build_cluster_record(0, [1, 2, 3], edges, neuron_info, 1)

    assert record is not None
    assert record["suggested_label"] == "NVM · Version · CLI"
    assert record["member_count"] == 3
    assert record["representative_labels"][0] == neuron_info[2]["label"]
    assert record["departments"] == ["Environment", "Harness", "Projects"]


def test_cluster_record_can_include_single_department_for_map_zones():
    neuron_info = {
        1: {"label": "Retrieval telemetry events", "department": "Projects"},
        2: {"label": "Retrieval depth telemetry", "department": "Projects"},
        3: {"label": "Adaptive retrieval trace", "department": "Projects"},
    }
    edges = [(1, 2, 0.9), (2, 3, 0.8)]

    assert _build_cluster_record(0, [1, 2, 3], edges, neuron_info, 2) is None
    assert _build_cluster_record(0, [1, 2, 3], edges, neuron_info, 1) is not None
