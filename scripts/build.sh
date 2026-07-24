#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$repo_root/src"
output_path="${1:-$repo_root/GitLab.alfredworkflow}"
build_dir="$(mktemp -d)"
archive_path="$build_dir/GitLab.alfredworkflow"

cleanup() {
    rm -f "$archive_path"
    rmdir "$build_dir"
}
trap cleanup EXIT

(
    cd "$source_dir"
    zip -X -q -r "$archive_path" . \
        -x '__pycache__/' '__pycache__/*' \
        '*/__pycache__/' '*/__pycache__/*' '*.pyc'
)

unzip -t "$archive_path" >/dev/null
mv "$archive_path" "$output_path"
rmdir "$build_dir"
trap - EXIT

printf 'Built %s\n' "$output_path"
