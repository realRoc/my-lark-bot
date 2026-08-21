#!/usr/bin/env bash
set -euo pipefail
umask 077

function_name=${FUNCTION_NAME:-daily-api-monitor}
region=ap-hongkong

fetch_tat_output() {
  local instance_id="$1" remote_command="$2" encoded response invocation status raw
  encoded=$(printf '%s' "$remote_command" | base64 | tr -d '\n')
  response=$(tccli tat RunCommand --region "$region" --InstanceIds "[\"$instance_id\"]" --Content "$encoded" --CommandType SHELL --Username root --Timeout 60)
  invocation=$(printf '%s' "$response" | python3 -c 'import json,sys; print(json.load(sys.stdin)["InvocationId"])')
  for _ in $(seq 1 20); do
    raw=$(tccli tat DescribeInvocationTasks --region "$region" --Filters "[{\"Name\":\"invocation-id\",\"Values\":[\"$invocation\"]}]" --HideOutput False)
    status=$(printf '%s' "$raw" | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("InvocationTaskSet") or [{}])[0].get("TaskStatus", ""))')
    if [[ "$status" == SUCCESS ]]; then
      printf '%s' "$raw" | python3 -c 'import base64,json,sys; d=json.load(sys.stdin); print(base64.b64decode(d["InvocationTaskSet"][0]["TaskResult"]["Output"]).decode(), end="")'
      return
    fi
    [[ "$status" == FAILED || "$status" == TIMEOUT ]] && return 1
    sleep 2
  done
  return 1
}

wait_for_active() {
  local function_state
  for _ in $(seq 1 30); do
    function_state=$(tccli scf GetFunction --region "$region" --FunctionName "$function_name" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Status"])')
    [[ "$function_state" == Active ]] && return
    [[ "$function_state" == Failed ]] && return 1
    sleep 2
  done
  return 1
}

export AMA_API_KEY
AMA_API_KEY=$(fetch_tat_output ins-jihl2bqa 'docker exec askmany-monitor_backend_1 printenv API_KEY' | tr -d '\r\n')
router_creds=$(fetch_tat_output ins-gch959xq 'docker exec teamocode-dashboard printenv DASHBOARD_USERNAME DASHBOARD_PASSWORD')
export ROUTER_USERNAME ROUTER_PASSWORD
ROUTER_USERNAME=$(printf '%s' "$router_creds" | sed -n '1p')
ROUTER_PASSWORD=$(printf '%s' "$router_creds" | sed -n '2p')
: "${FEISHU_WEBHOOK_URL:?set FEISHU_WEBHOOK_URL}"
export AMA_BASE_URL='http://43.159.11.241:8000'
export ROUTER_BASE_URL='https://op.teamocode.com'
: "${ASKMANY_USERNAME:?set ASKMANY_USERNAME}"
: "${ASKMANY_PASSWORD:?set ASKMANY_PASSWORD}"
export ASKMANY_BASE_URL="${ASKMANY_BASE_URL:-https://askmanyai.cn}"
export DRY_RUN="${DRY_RUN:-0}"

deploy_tmp=$(mktemp -d /tmp/daily-api-monitor-deploy.XXXXXX)
cleanup() {
  if [[ -n "${deploy_tmp:-}" && "$deploy_tmp" == /tmp/daily-api-monitor-deploy.* ]]; then
    find "$deploy_tmp" -depth -delete
  fi
}
trap cleanup EXIT
zip -j -q "$deploy_tmp/code.zip" index.py
base64 < "$deploy_tmp/code.zip" | tr -d '\n' > "$deploy_tmp/code.b64"
python3 - <<'PY' > "$deploy_tmp/environment.json"
import json, os
keys = ["AMA_BASE_URL", "AMA_API_KEY", "ROUTER_BASE_URL", "ROUTER_USERNAME", "ROUTER_PASSWORD", "FEISHU_WEBHOOK_URL", "ASKMANY_BASE_URL", "ASKMANY_USERNAME", "ASKMANY_PASSWORD", "DRY_RUN"]
print(json.dumps({"Variables": [{"Key": key, "Value": os.environ[key]} for key in keys]}))
PY
code_json=$(python3 -c 'import json,sys; print(json.dumps({"ZipFile": open(sys.argv[1]).read()}))' "$deploy_tmp/code.b64")
environment_json=$(tr -d '\n' < "$deploy_tmp/environment.json")
existing=$(tccli scf ListFunctions --region "$region" --Limit 100 | python3 -c 'import json,sys; name=sys.argv[1]; print(any(x.get("FunctionName")==name for x in json.load(sys.stdin).get("Functions",[])))' "$function_name")

if [[ "$existing" == True ]]; then
  tccli scf UpdateFunctionCode --region "$region" --FunctionName "$function_name" --Handler index.main_handler --Code "$code_json" >/dev/null
  wait_for_active
  tccli scf UpdateFunctionConfiguration --region "$region" --FunctionName "$function_name" --Timeout 240 --Environment "$environment_json" >/dev/null
  wait_for_active
  echo "updated $function_name"
else
  tccli scf CreateFunction --region "$region" --FunctionName "$function_name" --Description 'AMA and TeamoRouter API monitor to Feishu' --Code "$code_json" --Handler index.main_handler --Runtime Python3.10 --Timeout 240 --MemorySize 128 --Environment "$environment_json"
fi
