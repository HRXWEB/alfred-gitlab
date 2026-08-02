import ast
import re
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
V4_MODULES = (
    "cache_paths.py",
    "cache_records.py",
    "cache_state.py",
    "host_commands.py",
    "host_migration_rollback.py",
    "host_registry.py",
    "host_registry_state.py",
    "host_services.py",
    "host_values.py",
    "personal_pages.py",
    "project_search.py",
    "refresh_runtime.py",
    "search_refresh.py",
    "update.py",
)


def test_v4_modules_parse_as_python_3_9() -> None:
    for module_name in V4_MODULES:
        source = (SRC_DIR / module_name).read_text(encoding="utf-8")
        ast.parse(source, filename=module_name, feature_version=(3, 9))


def test_v4_dataclasses_do_not_require_python_3_10_slots() -> None:
    for module_name in V4_MODULES:
        source = (SRC_DIR / module_name).read_text(encoding="utf-8")
        assert "slots=True" not in source


def test_v4_runtime_annotations_do_not_use_pep604_inside_cast() -> None:
    pattern = re.compile(r"cast\([^\n)]*\|")
    for module_name in V4_MODULES:
        source = (SRC_DIR / module_name).read_text(encoding="utf-8")
        assert pattern.search(source) is None
