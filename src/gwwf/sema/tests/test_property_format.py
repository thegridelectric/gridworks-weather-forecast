from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from gwwf.sema import property_format


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFINITIONS_DIR = PACKAGE_ROOT / "definitions"
FORMAT_SCHEMA_PATHS = [
    DEFINITIONS_DIR / "formats" / "left.right.dot.yaml",
    DEFINITIONS_DIR / "formats" / "utc.seconds.yaml",
]
RUNTIME_FORMAT_TYPES: dict[str, Any] = {
    "left.right.dot": property_format.LeftRightDot,
    "utc.seconds": property_format.UTCSeconds,
}


def load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def format_adapter(format_name: str) -> TypeAdapter:
    return TypeAdapter(RUNTIME_FORMAT_TYPES[format_name])


@pytest.mark.parametrize("schema_path", FORMAT_SCHEMA_PATHS)
def test_property_format_schema_examples_validate(schema_path: Path) -> None:
    schema = load_yaml(schema_path)
    adapter = format_adapter(schema["title"])

    for example in schema.get("examples", []):
        assert adapter.validate_python(example) == example


@pytest.mark.parametrize("schema_path", FORMAT_SCHEMA_PATHS)
def test_property_format_schema_counterexamples_fail(schema_path: Path) -> None:
    schema = load_yaml(schema_path)
    adapter = format_adapter(schema["title"])

    for counterexample in schema.get("counterexamples", []):
        with pytest.raises((TypeError, ValueError, ValidationError)):
            adapter.validate_python(counterexample)
