#!/usr/bin/env bash
# Central: proves that no NVFlare hospital private key is on this machine.
#   bash deploy/central/nvflare/check_keys.sh [dir ...]
# Default scan: every ~/flhub-runtime/nvflare* runtime, ~/flhub-runtime/hospital-kits, ~/.nvflare,
# the files directly in $HOME, and this user's files in /tmp. Every file with PEM private key
# material is checked, also inside (nested) .tgz/.tar/.zip/.gz archives. A key is allowed only if
# its public key belongs to a root CA (ca/rootCA.pem), a server kit (startup/server.crt) or an admin
# kit (startup/client.crt next to fed_admin.json) under ~/flhub-runtime/nvflare*. Anything else,
# and any file that cannot be read or is too large to check, makes it exit 1.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
dirs=("$@")
if [ ${#dirs[@]} -eq 0 ]; then
  dirs=("$HOME"/flhub-runtime/nvflare* "$HOME/flhub-runtime/hospital-kits" "$HOME/.nvflare" "$HOME" /tmp)
fi
"$NVF_VENV/bin/python" - "$HOME" "${dirs[@]}" <<'PY'
import glob, gzip, hashlib, io, os, sys, tarfile, zipfile
from cryptography import x509
from cryptography.hazmat.primitives import serialization

home, roots = sys.argv[1], sys.argv[2:]
MARK, MAX = b"PRIVATE KEY", 256 << 20
uid = os.getuid()

def spki(pub):
    return hashlib.sha256(pub.public_bytes(serialization.Encoding.DER,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()

allowed = {}
rt = os.path.join(home, "flhub-runtime")
for pat, kind in (("nvflare*/**/ca/rootCA.pem", "root CA"), ("nvflare*/**/startup/server.crt", "server")):
    for p in glob.glob(os.path.join(rt, pat), recursive=True):
        allowed[spki(x509.load_pem_x509_certificate(open(p, "rb").read()).public_key())] = f"{kind} {p}"
for p in glob.glob(os.path.join(rt, "nvflare*/**/startup/fed_admin.json"), recursive=True):
    c = os.path.join(os.path.dirname(p), "client.crt")
    if os.path.exists(c):
        allowed[spki(x509.load_pem_x509_certificate(open(c, "rb").read()).public_key())] = f"admin {c}"

found, ok, bad = [], [], []
def key_hashes(data):
    """sha256 of the public key of every PEM private key in data (None if it cannot be parsed)."""
    out, i = [], 0
    while (i := data.find(b"-----BEGIN", i)) >= 0:
        end = data.find(b"-----END", i)
        stop = data.find(b"-----", end + 8) + 5 if end >= 0 else -1
        block = data[i:stop] if stop > 4 else data[i:]
        i = max(stop, i + 10)
        if MARK not in block.split(b"\n", 1)[0]:
            continue
        try:
            out.append(spki(serialization.load_pem_private_key(block, password=None).public_key()))
        except Exception:
            out.append(None)
    return out

def check(label, data, depth=0):
    if depth > 4:
        bad.append(f"{label} (archives nested too deep)"); return
    try:
        if data[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for i in z.infolist():
                    if i.is_dir(): continue
                    if i.file_size > MAX: bad.append(f"{label} :: {i.filename} (too large)"); continue
                    check(f"{label} :: {i.filename}", z.read(i), depth + 1)
            return
        if data[:2] == b"\x1f\x8b":
            raw = gzip.decompress(data)
            try:
                with tarfile.open(fileobj=io.BytesIO(raw)) as t:
                    members = t.getmembers()
                    for m in members:
                        if not m.isfile(): continue
                        if m.size > MAX: bad.append(f"{label} :: {m.name} (too large)"); continue
                        check(f"{label} :: {m.name}", t.extractfile(m).read(), depth + 1)
                return
            except tarfile.TarError:
                check(f"{label} (gunzipped)", raw, depth + 1); return
        if len(data) > 262 and data[257:262] == b"ustar":
            with tarfile.open(fileobj=io.BytesIO(data)) as t:
                for m in t.getmembers():
                    if m.isfile(): check(f"{label} :: {m.name}", t.extractfile(m).read(), depth + 1)
            return
    except Exception as e:
        bad.append(f"{label} (unreadable archive: {e})"); return
    if MARK not in data:
        return
    for h in key_hashes(data):
        if h in allowed: ok.append(f"{label}  [{allowed[h]}]")
        else: found.append(label + ("" if h else " (unparsable key)"))

scanned, seen = 0, set()
for root in roots:
    if not os.path.isdir(root): continue
    top_only = os.path.realpath(root) == os.path.realpath(home)   # $HOME itself: its files, not subdirs
    for d, sub, files in os.walk(root):
        if top_only: sub[:] = []
        for f in files:
            p = os.path.join(d, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not os.path.isfile(p) or os.path.islink(p) or st.st_uid != uid or (st.st_dev, st.st_ino) in seen:
                continue
            seen.add((st.st_dev, st.st_ino))
            scanned += 1
            if st.st_size > MAX:
                bad.append(f"{p} (too large to check)"); continue
            try:
                data = open(p, "rb").read()
            except OSError as e:
                bad.append(f"{p} (unreadable: {e})"); continue
            check(p, data)

print(f"check_keys: scanned {scanned} files under {' '.join(roots)}")
for p in ok: print(f"check_keys: allowed  {p}")
for p in found: print(f"check_keys: FOUND    {p}")
for p in bad: print(f"check_keys: UNCHECKED {p}")
if found or bad:
    print(f"check_keys: FAIL: {len(found)} private key(s) that are not the CA's, a server's or an admin's; {len(bad)} file(s) not checked")
    sys.exit(1)
print("check_keys: OK: no hospital private key on central")
PY
