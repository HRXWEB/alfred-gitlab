from pathlib import Path
import subprocess
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_build_is_deterministic_and_uses_fixed_metadata(tmp_path):
    first = tmp_path / "first.alfredworkflow"
    second = tmp_path / "second.alfredworkflow"

    subprocess.check_call([str(REPO_ROOT / "scripts/build.sh"), str(first)])
    subprocess.check_call([str(REPO_ROOT / "scripts/build.sh"), str(second)])

    assert first.read_bytes() == second.read_bytes()

    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert all(
            item.date_time == (1980, 1, 1, 0, 0, 0)
            for item in archive.infolist()
        )
