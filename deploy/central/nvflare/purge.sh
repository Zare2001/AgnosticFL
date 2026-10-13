#!/usr/bin/env bash
# Frees disk on central from NVFlare job data (PLAN.md §7; between E4a payload runs):
#  - finished jobs (any job not queued or running) are deleted from the server's job store
#    through the FLARE API (job definitions + their workspace archives);
#  - server job workspaces (<server kit>/<job uuid>/) of jobs that are no longer active.
# The running relay job, the server kit, the adapter and all run dirs are left alone. Idempotent.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/common.sh"
before=$(df -BM --output=avail / | tail -1 | tr -d ' M')
cd "$REPO"
"$NVF_VENV/bin/python" - "$NVF_ADMIN_KIT" "$NVF_SERVER_KIT" <<'PY'
import re, shutil, sys
from pathlib import Path
from adapters.nvflare.flare_admin import ACTIVE_JOB_STATES, FlareAdmin

admin_kit, server_kit = sys.argv[1], Path(sys.argv[2]).resolve()
admin = FlareAdmin(admin_kit)
try:
    jobs = admin.list_all_jobs()
    active = {j["job_id"] for j in jobs if j.get("status") in ACTIVE_JOB_STATES}
    for j in jobs:
        if j["job_id"] not in active:
            admin.delete_job(j["job_id"])
            print(f"purge: deleted job {j['job_id']} ({j.get('job_name')}, {j.get('status')}) from the job store")
    uuid = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    for d in server_kit.iterdir():
        if d.is_dir() and uuid.match(d.name) and d.name not in active and d.resolve().parent == server_kit:
            shutil.rmtree(d)
            print(f"purge: removed server workspace {d.name}")
    print(f"purge: kept active jobs {sorted(active)}")
finally:
    admin.close()
PY
after=$(df -BM --output=avail / | tail -1 | tr -d ' M')
echo "purge: OK, free disk ${before} MB -> ${after} MB"
