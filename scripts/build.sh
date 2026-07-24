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

python3 - "$repo_root" "$archive_path" <<'PY'
from pathlib import Path
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

repo_root = Path(sys.argv[1])
archive_path = Path(sys.argv[2])
records = subprocess.check_output(
    ['git', '-C', str(repo_root), 'ls-files', '--stage', '-z', '--', 'src'],
).split(b'\0')
tracked = []
for record in records:
    if not record:
        continue
    metadata, raw_path = record.split(b'\t', 1)
    mode, object_id, stage = metadata.decode('ascii').split()
    path = raw_path.decode('utf-8')
    if stage != '0':
        raise RuntimeError('Unmerged source path: {}'.format(path))
    if mode == '120000':
        raise RuntimeError('Refusing to package symlink: {}'.format(path))
    relative_path = Path(path).relative_to('src')
    if relative_path.suffix == '.pyc' or '__pycache__' in relative_path.parts:
        continue
    tracked.append((relative_path, mode, object_id))

with ZipFile(archive_path, 'w') as archive:
    for relative_path, tracked_mode, object_id in sorted(tracked):
        info = ZipInfo(relative_path.as_posix(), (1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = int(tracked_mode, 8) << 16
        info.compress_type = ZIP_DEFLATED
        content = subprocess.check_output(
            ['git', '-C', str(repo_root), 'cat-file', 'blob', object_id],
        )
        archive.writestr(info, content, compresslevel=9)
PY

unzip -t "$archive_path" >/dev/null
mv "$archive_path" "$output_path"
trap - EXIT
rm -rf "$build_dir"

printf 'Built %s\n' "$output_path"
