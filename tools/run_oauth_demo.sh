#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
venv=/opt/demo-oauth/venv
export YAMIN_OAUTH_CLIENT="${YAMIN_OAUTH_CLIENT:-/root/.hermes/yamin_oauth_client.json}"
export YAMIN_DEMO_TOKEN="${YAMIN_DEMO_TOKEN:-/root/.hermes/yamin_demo_token.json}"
export OAUTHLIB_INSECURE_TRANSPORT=1
export OAUTHLIB_RELAX_TOKEN_SCOPE=1

if [[ ! -x "$venv/bin/uvicorn" ]]; then
  echo "missing $venv (create it and pip install -r app/oauth_demo_requirements.txt)" >&2
  exit 1
fi

cd "$root"
exec "$venv/bin/uvicorn" app.oauth_demo:app --host 127.0.0.1 --port 8085
