from factori.schema_diff import SchemaChangeClassification, compare_json_schemas


def test_added_optional_property_is_nonbreaking() -> None:
    old = _object_schema({"name": {"type": "string"}}, required=["name"])
    new = _object_schema(
        {"name": {"type": "string"}, "note": {"type": "string"}},
        required=["name"],
    )

    result = compare_json_schemas(old, new, schema_name="example.schema.json")

    assert _classes(result) == [SchemaChangeClassification.NON_BREAKING]
    assert "optional" in result.changes[0].message


def test_added_required_property_is_breaking() -> None:
    old = _object_schema({"name": {"type": "string"}}, required=["name"])
    new = _object_schema(
        {"name": {"type": "string"}, "count": {"type": "integer"}},
        required=["name", "count"],
    )

    result = compare_json_schemas(old, new, schema_name="example.schema.json")

    assert _classes(result) == [SchemaChangeClassification.BREAKING]
    assert "required" in result.changes[0].message


def test_removed_required_and_optional_properties_are_breaking() -> None:
    old = _object_schema(
        {"name": {"type": "string"}, "note": {"type": "string"}},
        required=["name"],
    )
    required_removed = _object_schema({"note": {"type": "string"}})
    optional_removed = _object_schema({"name": {"type": "string"}}, required=["name"])

    first = compare_json_schemas(old, required_removed, schema_name="example.schema.json")
    second = compare_json_schemas(old, optional_removed, schema_name="example.schema.json")

    assert any(
        change.classification == SchemaChangeClassification.BREAKING
        for change in first.changes
    )
    assert any(
        change.classification == SchemaChangeClassification.BREAKING
        for change in second.changes
    )
    assert any("optional" in change.message for change in second.changes)


def test_incompatible_type_change_is_breaking() -> None:
    old = _object_schema({"value": {"type": "string"}})
    new = _object_schema({"value": {"type": "integer"}})

    result = compare_json_schemas(old, new, schema_name="example.schema.json")

    assert _classes(result) == [SchemaChangeClassification.BREAKING]


def test_enum_removal_is_breaking_and_addition_is_nonbreaking() -> None:
    old = {"type": "string", "enum": ["A", "B"]}
    removed = {"type": "string", "enum": ["A"]}
    added = {"type": "string", "enum": ["A", "B", "C"]}

    removal = compare_json_schemas(old, removed, schema_name="enum.schema.json")
    addition = compare_json_schemas(old, added, schema_name="enum.schema.json")

    assert _classes(removal) == [SchemaChangeClassification.BREAKING]
    assert _classes(addition) == [SchemaChangeClassification.NON_BREAKING]


def test_description_only_change_is_documentation_only() -> None:
    old = {"type": "string", "title": "Old", "description": "Old description"}
    new = {"type": "string", "title": "New", "description": "New description"}

    result = compare_json_schemas(old, new, schema_name="docs.schema.json")

    assert len(result.changes) == 2
    assert set(_classes(result)) == {SchemaChangeClassification.DOCUMENTATION_ONLY}


def test_stricter_and_less_restrictive_constraints_are_classified() -> None:
    old = {"type": "number", "minimum": 0, "maximum": 10}
    stricter = {"type": "number", "minimum": 1, "maximum": 9}
    looser = {"type": "number", "minimum": -1, "maximum": 11}

    strict_result = compare_json_schemas(old, stricter, schema_name="number.schema.json")
    loose_result = compare_json_schemas(old, looser, schema_name="number.schema.json")

    assert set(_classes(strict_result)) == {SchemaChangeClassification.BREAKING}
    assert set(_classes(loose_result)) == {SchemaChangeClassification.NON_BREAKING}


def test_complex_composition_change_is_unknown() -> None:
    old = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    new = {"anyOf": [{"type": "integer"}, {"type": "null"}]}

    result = compare_json_schemas(old, new, schema_name="complex.schema.json")

    assert _classes(result) == [SchemaChangeClassification.UNKNOWN]


def _object_schema(
    properties: dict[str, dict[str, object]],
    required: list[str] | None = None,
) -> dict[str, object]:
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def _classes(result):
    return [change.classification for change in result.changes]
