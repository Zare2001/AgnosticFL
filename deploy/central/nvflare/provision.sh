#!/usr/bin/env bash
# Provisions the NVFlare project: server $NVF_SERVER_NAME (fed 8002, admin 8003), one client
# per NVFlare hospital, one project_admin user for the adapter. Packs each hospital kit as
# $NVF_KITS_OUT/<site>.tgz for transfer with `tools/bus put --secret`.
# Idempotent: an existing provisioning is kept (stable certificates) unless --force is given.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"

force=0
[ "${1:-}" = "--force" ] && force=1

if [ -d "$NVF_SERVER_KIT/startup" ] && [ "$force" = 0 ]; then
  echo "provision: kept existing kits in $NVF_PROD (use --force to re-provision)"
else
  rm -rf "$NVF_WS"
  mkdir -p "$NVF_RUNTIME" && chmod 700 "$NVF_RUNTIME"
  {
    echo "api_version: 3"
    echo "name: $NVF_PROJECT"
    echo "description: AgnosticFL NVFlare network operated by the flhub central VM"
    echo "participants:"
    echo "  - name: $NVF_SERVER_NAME"
    echo "    type: server"
    echo "    org: flhub"
    echo "    fed_learn_port: $NVF_FL_PORT"
    echo "    admin_port: $NVF_ADMIN_PORT"
    i=0
    for site in $NVF_SITES; do
      i=$((i + 1))
      echo "  - name: $site"
      echo "    type: client"
      echo "    org: hospital$i"
    done
    echo "  - name: $NVF_ADMIN_USER"
    echo "    type: admin"
    echo "    org: flhub"
    echo "    role: project_admin"
    echo "builders:"
    echo "  - path: nvflare.lighter.impl.workspace.WorkspaceBuilder"
    echo "    args:"
    echo "      template_file: master_template.yml"
    echo "  - path: nvflare.lighter.impl.static_file.StaticFileBuilder"
    echo "    args:"
    echo "      config_folder: config"
    echo "  - path: nvflare.lighter.impl.cert.CertBuilder"
    echo "  - path: nvflare.lighter.impl.signature.SignatureBuilder"
  } > "$NVF_RUNTIME/project.yml"
  "$NVF_VENV/bin/nvflare" provision -p "$NVF_RUNTIME/project.yml" -w "$NVF_WS" --force > "$NVF_RUNTIME/provision.log" 2>&1 \
    || { tail -20 "$NVF_RUNTIME/provision.log"; exit 1; }
  echo "provision: new kits in $NVF_PROD (server name $NVF_SERVER_NAME)"
fi

mkdir -p "$NVF_KITS_OUT" && chmod 700 "$NVF_KITS_OUT"
for site in $NVF_SITES; do
  tar -czf "$NVF_KITS_OUT/$site.tgz" -C "$NVF_PROD" "$site"
  chmod 600 "$NVF_KITS_OUT/$site.tgz"
done
echo "provision: hospital kits: $(cd "$NVF_KITS_OUT" && ls ./*.tgz | tr '\n' ' ')"
echo "provision: send each with: tools/bus put $NVF_KITS_OUT/<site>.tgz --to nvflare --secret"
