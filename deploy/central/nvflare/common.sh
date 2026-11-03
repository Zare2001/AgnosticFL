# Shared settings for deploy/central/nvflare/*.sh (sourced, not executed).
# Override any NVF_* variable in the environment, e.g. NVF_SERVER_NAME=localhost for an island.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
. "$REPO/deploy/topology.env"
[ -f "$REPO/deploy/versions.env" ] && . "$REPO/deploy/versions.env"

NVF_VENV="${NVF_VENV:-$HOME/venvs/nvflare}"
NVF_RUNTIME="${NVF_RUNTIME:-$HOME/flhub-runtime/nvflare/server}"
NVF_SERVER_NAME="${NVF_SERVER_NAME:-$FLHUB_CENTRAL_HOSTNAME}"
NVF_PROJECT="${NVF_PROJECT:-flhub}"
NVF_ADMIN_USER="${NVF_ADMIN_USER:-admin@flhub.local}"
NVF_FL_PORT="${NVF_FL_PORT:-$NVFLARE_FL_PORT}"
NVF_ADMIN_PORT="${NVF_ADMIN_PORT:-$NVFLARE_ADMIN_PORT}"
NVF_ADAPTER_PORT="${NVF_ADAPTER_PORT:-$ADAPTER_NVFLARE_PORT}"
NVF_HUB_URL="${NVF_HUB_URL:-http://127.0.0.1:$HUB_CORE_PORT}"
# NVFlare hospitals from the topology: "nvf-h1 nvf-h2" (the base sites; status.sh needs them online).
# NVF_EXTRA_SITES (e.g. "nvf-h7 nvf-h8" for E5) are served as well but may stay offline.
NVF_BASE_SITES="${NVF_BASE_SITES:-$(for s in $SITES; do IFS=: read -r id fw _ <<<"$s"; if [ "$fw" = nvflare ]; then printf '%s ' "$id"; fi; done)}"
NVF_BASE_SITES="${NVF_BASE_SITES% }"
NVF_EXTRA_SITES="${NVF_EXTRA_SITES-nvf-h7 nvf-h8}"  # E5 extras: registered, offline outside E5
NVF_SITES="${NVF_SITES:-$NVF_BASE_SITES${NVF_EXTRA_SITES:+ $NVF_EXTRA_SITES}}"

NVF_WS="$NVF_RUNTIME/ws"
NVF_PROD="$NVF_WS/$NVF_PROJECT/prod_00"
NVF_SERVER_KIT="$NVF_PROD/$NVF_SERVER_NAME"
NVF_ADMIN_KIT="$NVF_PROD/$NVF_ADMIN_USER"
NVF_KITS_OUT="$NVF_RUNTIME/kits"   # stage-1 centrally provisioned kits (removed at the switch-over)
# Distributed provisioning (NVFlare 2.9 `nvflare cert`): central is project admin with its own root CA.
# Only the CA key, the server key and the adapter's admin key exist on central; hospitals send a
# request zip (CSR, no key) and get back a signed zip (their certificate + rootCA.pem).
NVF_CA_DIR="$NVF_RUNTIME/ca"                 # rootCA.pem, rootCA.key, ca.json (mode 700)
NVF_PROFILE="$NVF_RUNTIME/project_profile.yaml"
NVF_REQ_DIR="$NVF_RUNTIME/requests"          # the server's and the admin's own requests (their keys)
NVF_SIGNED_OUT="$NVF_RUNTIME/signed"         # <site>.signed.zip to return to the hospital
NVF_ISSUED="$NVF_RUNTIME/issued"             # <site>.crt: last certificate issued per hospital
NVF_ADMIN_ROLE="${NVF_ADMIN_ROLE:-lead}"     # distributed provisioning has no project_admin users
NVF_SCHEME="${NVF_SCHEME:-http}"
NVF_CONN_SEC="${NVF_CONN_SEC:-mtls}"
NVF_JOBS="$NVF_RUNTIME/jobs"
NVF_LOGS="$NVF_RUNTIME/logs"
NVF_ADAPTER_PID="$NVF_RUNTIME/adapter.pid"

nvf_port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
nvf_pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

# SHA256 fingerprint of the root CA, in the form `nvflare package --fingerprint` takes.
nvf_ca_fingerprint() { openssl x509 -in "$NVF_CA_DIR/rootCA.pem" -noout -fingerprint -sha256 | cut -d= -f2; }
# Expected org of a hospital: nvf-h<N>... -> hospital<N> (as the stage-1 provisioning named them).
nvf_org_of() { local n; n=$(sed -n 's/^nvf-h\([0-9][0-9]*\).*/\1/p' <<<"$1"); [ -n "$n" ] && echo "hospital$n"; }
