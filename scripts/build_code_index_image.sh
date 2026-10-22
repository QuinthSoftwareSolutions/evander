#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
  cat <<'HELP'
Build and save the isolated repository indexing image; does not deploy it.
Requires Docker, Git, .NET SDK 10.0.113, Node 24.22.1 and Evander's Python 3.12 venv.
Tool downloads happen on the host, before an offline Docker build.
Environment:
  CODE_INDEX_CACHE          Download/build cache (default: dist/code-index-tools)
  CODE_INDEX_IMAGE          Image tag (default: evander-code-index:1)
  CODE_INDEX_ARCHIVE        Output archive (default: dist/evander-code-index.tar)
  CODE_INDEX_RUNNER_IMAGE   Existing base (default: evander-runner:latest)
  CODE_INDEX_DOTNET_ROOT    Installed SDK root (default: resolved dotnet directory)
  CODE_INDEX_NODE_ROOT      Installed Node root (default: resolved node parent)
  CODE_INDEX_PYTHON         Python 3.12 with pydantic (default: .venv/bin/python)
  DOCKER                   Docker command (default: docker)
HELP
  exit 0
fi
[[ $# == 0 ]] || { echo 'Only --help is supported' >&2; exit 2; }
INDEX_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INDEX_CACHE="${CODE_INDEX_CACHE:-${INDEX_ROOT}/dist/code-index-tools}"
INDEX_IMAGE="${CODE_INDEX_IMAGE:-evander-code-index:1}"
INDEX_ARCHIVE="${CODE_INDEX_ARCHIVE:-${INDEX_ROOT}/dist/evander-code-index.tar}"
INDEX_PYTHON="${CODE_INDEX_PYTHON:-${INDEX_ROOT}/.venv/bin/python}"
INDEX_DOTNET_ROOT="${CODE_INDEX_DOTNET_ROOT:-$(dirname "$(readlink -f "$(command -v dotnet)")")}"
INDEX_NODE_ROOT="${CODE_INDEX_NODE_ROOT:-$(dirname "$(dirname "$(readlink -f "$(command -v node)")")")}"
INDEX_DOCKER="${DOCKER:-docker}"
INDEX_SCIP_REVISION=47884461a79839fb74c99e6a0a7978cd7eb62476
[[ "$(${INDEX_DOTNET_ROOT}/dotnet --version)" == 10.0.113 ]] || { echo 'SDK 10.0.113 required' >&2; exit 1; }
[[ "$(${INDEX_NODE_ROOT}/bin/node --version)" == v24.22.1 ]] || { echo 'Node 24.22.1 required' >&2; exit 1; }
mkdir -p "${INDEX_CACHE}" "$(dirname "${INDEX_ARCHIVE}")"
if [[ ! -d "${INDEX_CACHE}/source/.git" ]]; then
  git clone --no-checkout https://github.com/sourcegraph/scip-dotnet.git "${INDEX_CACHE}/source"
fi
git -C "${INDEX_CACHE}/source" checkout --detach "${INDEX_SCIP_REVISION}"
[[ -z "$(git -C "${INDEX_CACHE}/source" status --porcelain --untracked-files=no)" ]] || { echo 'Indexer source has tracked changes' >&2; exit 1; }
cat >"${INDEX_CACHE}/nuget.config" <<'NUGET'
<configuration><packageSources><clear/><add key="nuget.org" value="https://api.nuget.org/v3/index.json"/></packageSources></configuration>
NUGET
"${INDEX_DOTNET_ROOT}/dotnet" publish "${INDEX_CACHE}/source/ScipDotnet/ScipDotnet.csproj" -c Release -f net10.0 --configfile "${INDEX_CACHE}/nuget.config" -o "${INDEX_CACHE}/dotnet-indexer"
cp "${INDEX_CACHE}/source/LICENSE" "${INDEX_CACHE}/dotnet-indexer/SCIP-LICENSE"
mkdir -p "${INDEX_CACHE}/typescript"
cp "${INDEX_ROOT}/docker/evander-code-index/"package*.json "${INDEX_CACHE}/typescript/"
"${INDEX_NODE_ROOT}/bin/npm" ci --prefix "${INDEX_CACHE}/typescript" --ignore-scripts --no-audit --no-fund
INDEX_CONTEXT="$(mktemp -d)"
trap 'rm -rf "${INDEX_CONTEXT}"' EXIT
cp "${INDEX_ROOT}/docker/evander-code-index/Dockerfile" "${INDEX_CONTEXT}/"
cp -a "${INDEX_DOTNET_ROOT}" "${INDEX_CONTEXT}/dotnet"
mkdir -p "${INDEX_CONTEXT}/node/bin" "${INDEX_CONTEXT}/node/lib/node_modules"
cp "${INDEX_NODE_ROOT}/bin/node" "${INDEX_CONTEXT}/node/bin/"
cp "${INDEX_NODE_ROOT}/LICENSE" "${INDEX_CONTEXT}/node/LICENSE"
cp -a "${INDEX_NODE_ROOT}/lib/node_modules/npm" "${INDEX_CONTEXT}/node/lib/node_modules/"
ln -s ../lib/node_modules/npm/bin/npm-cli.js "${INDEX_CONTEXT}/node/bin/npm"
cp -a "${INDEX_CACHE}/dotnet-indexer" "${INDEX_CONTEXT}/dotnet-indexer"
cp -a "${INDEX_CACHE}/typescript" "${INDEX_CONTEXT}/typescript"
"${INDEX_PYTHON}" - "${INDEX_ROOT}" "${INDEX_CONTEXT}" <<'PYTHON'
import importlib, json, pathlib, shutil, sys
root, stage = map(pathlib.Path, sys.argv[1:])
assert sys.version_info[:2] == (3, 12), 'The runner image uses Python 3.12'
destination = stage / 'python'
destination.mkdir()
for name in ('pydantic', 'pydantic_core', 'annotated_types', 'typing_extensions', 'typing_inspection'):
    source = pathlib.Path(importlib.import_module(name).__file__)
    if source.name == '__init__.py':
        shutil.copytree(source.parent, destination / name, ignore=shutil.ignore_patterns('__pycache__'))
    else:
        shutil.copy2(source, destination / source.name)
package = destination / 'evander'
package.mkdir()
(package / '__init__.py').write_text('')
shutil.copytree(root / 'src/evander/code_index', package / 'code_index', ignore=shutil.ignore_patterns('__pycache__'))
from evander.code_index.models import TOOLCHAIN
(stage / 'manifest.json').write_text(json.dumps(TOOLCHAIN, indent=2))
PYTHON
cat >"${INDEX_CONTEXT}/scip-dotnet" <<'WRAPPER'
#!/bin/sh
exec /opt/code-index/dotnet/dotnet /opt/code-index/dotnet-indexer/scip-dotnet.dll "$@"
WRAPPER
"${INDEX_DOCKER}" build --network=none --build-arg "RUNNER_IMAGE=${CODE_INDEX_RUNNER_IMAGE:-evander-runner:latest}" -t "${INDEX_IMAGE}" "${INDEX_CONTEXT}"
"${INDEX_DOCKER}" save -o "${INDEX_ARCHIVE}.tmp" "${INDEX_IMAGE}"
mv "${INDEX_ARCHIVE}.tmp" "${INDEX_ARCHIVE}"
"${INDEX_DOCKER}" image inspect "${INDEX_IMAGE}" --format '{{.Id}}' >"${INDEX_ARCHIVE}.image-id"
cp "${INDEX_CONTEXT}/manifest.json" "${INDEX_ARCHIVE}.toolchain.json"
echo "Built ${INDEX_IMAGE}; saved ${INDEX_ARCHIVE}"
