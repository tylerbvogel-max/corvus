import pytest

from app.services.schema_authority import (
    SchemaAuthorityError,
    expected_schema_heads,
    require_schema_heads,
)


def test_packaged_schema_has_one_expected_head():
    assert expected_schema_heads() == ("026_delivery_pathways",)


def test_matching_schema_head_is_accepted():
    status = require_schema_heads(
        ("026_delivery_pathways",), ("026_delivery_pathways",)
    )

    assert status.current_heads == status.expected_heads


@pytest.mark.parametrize(
    ("current", "expected_fragment"),
    [
        ((), "unmanaged (no alembic_version)"),
        (("024_cache_coherence",), "024_cache_coherence"),
        (("future_revision",), "future_revision"),
    ],
)
def test_unmanaged_behind_and_ahead_schemas_fail_closed(current, expected_fragment):
    with pytest.raises(SchemaAuthorityError) as exc_info:
        require_schema_heads(current, ("026_delivery_pathways",))

    message = str(exc_info.value)
    assert expected_fragment in message
    assert "alembic upgrade head" in message
    assert "026_delivery_pathways" in message
