#!/usr/bin/env bash

azure_https_origin_url() {
  local origin_url="$1"
  ORIGIN_URL_INPUT="${origin_url}" python3 - <<'PY'
import os
import re
import urllib.parse

origin_url = os.environ["ORIGIN_URL_INPUT"]
ssh_match = re.match(r"^git@ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/]+)$", origin_url)
https_match = re.match(
    r"^https://(?:[^@/]+@)?dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/]+)$",
    origin_url,
)
vs_match = re.match(
    r"^https://(?:[^@/]+@)?([^.]+)\.visualstudio\.com/([^/]+)/_git/([^/]+)$",
    origin_url,
)

match = ssh_match or https_match or vs_match
if not match:
    print("", end="")
else:
    organization, project, repo = match.groups()
    organization = urllib.parse.unquote(organization)
    project = urllib.parse.unquote(project)
    repo = urllib.parse.unquote(repo)
    print(
        "https://dev.azure.com/"
        f"{urllib.parse.quote(organization, safe='')}/"
        f"{urllib.parse.quote(project, safe='')}/_git/"
        f"{urllib.parse.quote(repo, safe='')}",
        end="",
    )
PY
}

git_provider_prepare_remote() {
  local remote_url="$1"
  local azure_pat_env="${EVANDER_AZURE_DEVOPS_PR_PAT_ENV:-AZURE_DEVOPS_PR_PAT}"
  local azure_pat_value="${!azure_pat_env:-}"
  local https_remote_url=""

  GIT_PROVIDER_REMOTE_URL="${remote_url}"
  GIT_PROVIDER_HTTP_EXTRA_HEADER=""

  if [[ -n "${azure_pat_value}" ]]; then
    https_remote_url="$(azure_https_origin_url "${remote_url}")"
    if [[ -n "${https_remote_url}" ]]; then
      GIT_PROVIDER_REMOTE_URL="${https_remote_url}"
      GIT_PROVIDER_HTTP_EXTRA_HEADER="$(
        AZURE_DEVOPS_PR_PAT_VALUE="${azure_pat_value}" python3 - <<'PY'
import base64
import os

pat = os.environ["AZURE_DEVOPS_PR_PAT_VALUE"]
encoded = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
print(f"Authorization: Basic {encoded}", end="")
PY
      )"
    fi
  fi
}

git_run_with_remote_auth() {
  local remote_url="$1"
  shift

  git_provider_prepare_remote "${remote_url}"
  if [[ -n "${GIT_PROVIDER_HTTP_EXTRA_HEADER}" ]]; then
    git -c "http.extraHeader=${GIT_PROVIDER_HTTP_EXTRA_HEADER}" "$@"
  else
    git "$@"
  fi
}

git_fetch_remote_with_auth() {
  local repo_path="$1"
  local remote_name="$2"
  shift 2

  local remote_url
  remote_url="$(git -C "${repo_path}" remote get-url "${remote_name}")"
  git_provider_prepare_remote "${remote_url}"

  local -a command=(
    git
    -C
    "${repo_path}"
  )

  if [[ "${GIT_PROVIDER_REMOTE_URL}" != "${remote_url}" ]]; then
    command+=(-c "url.${GIT_PROVIDER_REMOTE_URL}.insteadOf=${remote_url}")
  fi
  if [[ -n "${GIT_PROVIDER_HTTP_EXTRA_HEADER}" ]]; then
    command+=(-c "http.extraHeader=${GIT_PROVIDER_HTTP_EXTRA_HEADER}")
  fi

  command+=(fetch "$@" "${remote_name}")
  "${command[@]}"
}
