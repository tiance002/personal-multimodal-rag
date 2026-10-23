#!/bin/bash
set -euo pipefail
umask 077
[[ $EUID -eq 0 ]] || { echo 'root required' >&2; exit 78; }
for command in systemctl systemd-run systemd-analyze python3 findmnt lsblk install; do
  command -v "$command" >/dev/null || { echo "missing dependency: $command" >&2; exit 78; }
done
[[ -f /sys/fs/cgroup/cgroup.controllers && -b /dev/nvme0n1 ]] || { echo 'cgroup v2 and verified device required' >&2; exit 78; }
root_device=$(findmnt -n -o SOURCE /)
[[ $root_device == /dev/nvme0n1p3 ]] || { echo 'root device identity mismatch' >&2; exit 78; }
[[ $(lsblk -n -o PKNAME "$root_device") == nvme0n1 ]] || { echo 'root parent mismatch' >&2; exit 78; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
target_dir=/opt/rag-s8/diagnostics/common
install -d -m 0700 "$target_dir" "$target_dir/receipts"
# Existing material is preserved before replacing these two owned files only.
stamp=$(date -u +%Y%m%dT%H%M%SZ)-$$
for target in "$target_dir/rag-diagnostic-run" /etc/systemd/system/rag-diagnostics.slice; do
  if [[ -e $target ]]; then cp -p -- "$target" "$target.before-$stamp"; fi
done
install -m 0700 "$source_dir/rag-diagnostic-run" "$target_dir/rag-diagnostic-run"
install -m 0644 "$source_dir/rag-diagnostics.slice" /etc/systemd/system/rag-diagnostics.slice
systemd-analyze verify /etc/systemd/system/rag-diagnostics.slice
systemctl daemon-reload
systemctl start rag-diagnostics.slice
"$target_dir/rag-diagnostic-run" --verify
echo 'INSTALLED_AND_CGROUP_LIMITS_VERIFIED; production services untouched'
