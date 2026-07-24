from pathlib import Path
import sys

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

import cache_state


class FakeWorkflow:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.cache_writes = []

    def datafile(self, name):
        return str(self.data_dir / name)

    def cache_data(self, name, value):
        self.cache_writes.append((name, value))


def test_invalidation_changes_generation_and_clears_projects(tmp_path):
    workflow = FakeWorkflow(tmp_path)

    before = cache_state.current_generation(workflow)
    cache_state.invalidate_projects(workflow)
    after = cache_state.current_generation(workflow)

    assert after != before
    assert workflow.cache_writes == [("projects", None)]


def test_old_generation_cannot_write_projects(tmp_path):
    workflow = FakeWorkflow(tmp_path)
    old_generation = cache_state.current_generation(workflow)

    cache_state.invalidate_projects(workflow)
    written = cache_state.store_projects(
        workflow,
        old_generation,
        [{"id": 1}],
    )

    assert written is False
    assert workflow.cache_writes == [("projects", None)]
