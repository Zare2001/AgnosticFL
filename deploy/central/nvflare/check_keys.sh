#!/usr/bin/env bash
# Central: proves that no NVFlare hospital private key is on this machine.
#   bash deploy/central/nvflare/check_keys.sh [dir ...]
# Scans $NVF_RUNTIME, ~/flhub-runtime/hospital-kits and ~/.nvflare (or the dirs given): every file
# with PEM private key material, also inside .tgz/.tar/.zip archives. Allowed are only the root CA
# key, the server's key and the adapter admin's key of this project. Exit 1 if anything else is found.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
dirs=("$@")
[ ${#dirs[@]} -gt 0 ] || dirs=("$NVF_RUNTIME" "$HOME/flhub-runtime/hospital-kits" "$HOME/.nvflare")
"$NVF_VENV/bin/python" - "$NVF_CA_DIR/rootCA.key" "$NVF_SERVER_KIT/startup/server.key" \
  "$NVF_ADMIN_KIT/startup/client.key" -- "${dirs[@]}" <<'PY'
import os, sys, tarfile, zipfile
args = sys.argv[1:]
allowed = {os.path.realpath(p) for p in args[:args.index("--")]}
roots = args[args.index("--") + 1:]
MARK = b"PRIVATE KEY"
found, ok, scanned = [], [], 0

def archive_members(path):
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                for i in z.infolist():
                    if not i.is_dir() and i.file_size < 64 << 20:
                        yield i.filename, z.read(i)
        elif tarfile.is_tarfile(path):
            with tarfile.open(path) as t:
                for m in t.getmembers():
                    if m.isfile() and m.size < 64 << 20:
                        yield m.name, t.extractfile(m).read()
    except Exception as e:
        print(f"check_keys: cannot read archive {path}: {e}", file=sys.stderr)

for root in roots:
    if not os.path.isdir(root):
        continue
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            if os.path.islink(p) or not os.path.isfile(p) or os.path.getsize(p) > 256 << 20:
                continue
            scanned += 1
            if f.endswith((".tgz", ".tar.gz", ".tar", ".zip")):
                for name, data in archive_members(p):
                    if MARK in data:
                        found.append(f"{p} :: {name}")
                continue
            with open(p, "rb") as fh:
                if MARK not in fh.read():
                    continue
            (ok if os.path.realpath(p) in allowed else found).append(p)

print(f"check_keys: scanned {scanned} files under {' '.join(roots)}")
for p in ok:
    print(f"check_keys: allowed  {p}")
for p in found:
    print(f"check_keys: FOUND    {p}")
if found:
    print(f"check_keys: FAIL: {len(found)} private key(s) on central that are not the CA's, the server's or the admin's")
    sys.exit(1)
print("check_keys: OK: no hospital private key on central")
PY
