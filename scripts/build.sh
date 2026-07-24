#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$repo_root/src"
output_path="${1:-$repo_root/GitLab.alfredworkflow}"
build_dir="$(mktemp -d)"
archive_path="$build_dir/GitLab.alfredworkflow"

cleanup() {
    rm -rf "$build_dir"
}
trap cleanup EXIT

python3 - "$repo_root" "$source_dir" "$archive_path" <<'PY'
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

repo_root = Path(sys.argv[1])
source_dir = Path(sys.argv[2])
archive_path = Path(sys.argv[3])
tracked = subprocess.check_output(
    ['git', '-C', str(repo_root), 'ls-files', '--', 'src'],
    text=True,
).splitlines()
source_paths = sorted(
    Path(path).relative_to('src')
    for path in tracked
    if not path.endswith('.pyc') and '__pycache__' not in Path(path).parts
)

with ZipFile(archive_path, 'w') as archive:
    for relative_path in source_paths:
        source_path = source_dir / relative_path
        if source_path.is_symlink():
            raise RuntimeError('Refusing to package symlink: {}'.format(relative_path))

        info = ZipInfo(relative_path.as_posix(), (1980, 1, 1, 0, 0, 0))
        mode = 0o100755 if os.access(source_path, os.X_OK) else 0o100644
        info.create_system = 3
        info.external_attr = mode << 16
        info.compress_type = ZIP_DEFLATED
        archive.writestr(info, source_path.read_bytes(), compresslevel=9)
PY

unzip -t "$archive_path" >/dev/null
mv "$archive_path" "$output_path"
trap - EXIT
rm -rf "$build_dir"

printf 'Built %s\n' "$output_path"
