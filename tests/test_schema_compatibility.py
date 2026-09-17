"""Every model-facing schema must survive strict structured-output conversion.

A provider enforcing strict structured output requires each object in the schema to declare its
properties explicitly and to list every one of them as required. An open-ended mapping cannot be
expressed that way: the converter drops the property and the request is then rejected for naming a
required key that no longer exists. The failure arrives from the API at run time, on a step that
may only execute when something else has already gone wrong, so it can sit undetected for a long
while and then destroy a real run.

`ProductBRD` is round-tripped through the model by the repair step, so this applies to it as much
as to the schemas a step writes fresh.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentic.schemas.registry import SCHEMAS

# Schemas a model is asked to produce. Every one is named as an `output_schema` in a manifest.
MODEL_FACING = ["BRDItem", "BRDFragment", "ProductBRD", "TopTier"]


def _open_ended_objects(node: Any, path: str = "") -> list[str]:
    """Paths of objects that accept arbitrary keys rather than declaring their properties."""
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            extra = node.get("additionalProperties")
            if extra not in (False, None) and not node.get("properties"):
                found.append(path or "<root>")
        for key, value in node.items():
            # `properties` keys are field names, not schema keywords, so keep them in the path.
            prefix = f"{path}.{key}" if path else key
            found += _open_ended_objects(value, prefix)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _open_ended_objects(value, f"{path}[{index}]")
    return found


@pytest.mark.parametrize("name", MODEL_FACING)
def test_no_model_facing_schema_uses_an_open_ended_mapping(name) -> None:
    schema = SCHEMAS[name].model_json_schema()
    offenders = _open_ended_objects(schema)
    assert offenders == [], (
        f"{name} contains an open-ended object at {offenders}. A strict provider cannot express "
        "one; use a list of typed entries instead of a mapping."
    )


def test_area_headings_survive_the_round_trip() -> None:
    """The regression: `area_names` was a dict and broke every repair call with a 400."""
    from agentic.schemas.brd import AreaLabel, ProductBRD

    brd = ProductBRD(areas=[AreaLabel(code="SQL", name="Database questions")])
    restored = ProductBRD.model_validate(brd.model_dump(mode="json"))
    assert restored.area_names == {"SQL": "Database questions"}
