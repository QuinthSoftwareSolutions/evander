#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'USAGE'
Usage: build_mssql_client_toolchain_archive.sh --output <archive.tar.gz>

Build an offline SQL Server client-toolchain archive for container workers.

Environment:
  HOST_MSSQL_TOOLS_ROOT   SQL Server client tools root.
                          Default: /opt/mssql-tools18
  HOST_MSSQL_ODBC_ROOT    Microsoft ODBC Driver 18 root.
                          Default: /opt/microsoft/msodbcsql18
  HOST_MSSQL_RUNTIME_LIBS Space-delimited runtime libraries copied into the archive.
                          Default: libodbc.so.2, libodbcinst.so.2, libltdl.so.7
USAGE
}

output_path=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      output_path="${2:-}"
      shift 2
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      show_help >&2
      exit 1
      ;;
  esac
done

if [[ -z "${output_path}" ]]; then
  echo "--output is required" >&2
  exit 1
fi

tools_root="${HOST_MSSQL_TOOLS_ROOT:-/opt/mssql-tools18}"
odbc_root="${HOST_MSSQL_ODBC_ROOT:-/opt/microsoft/msodbcsql18}"
runtime_libs=(
  ${HOST_MSSQL_RUNTIME_LIBS:-/lib/x86_64-linux-gnu/libodbc.so.2 /lib/x86_64-linux-gnu/libodbcinst.so.2 /lib/x86_64-linux-gnu/libltdl.so.7}
)

if [[ ! -x "${tools_root}/bin/sqlcmd" ]]; then
  echo "Missing SQL Server client: ${tools_root}/bin/sqlcmd" >&2
  exit 1
fi
if [[ ! -d "${odbc_root}/lib64" ]]; then
  echo "Missing ODBC Driver 18 library directory: ${odbc_root}/lib64" >&2
  exit 1
fi

driver_library="$(find "${odbc_root}/lib64" -maxdepth 1 -type f -name 'libmsodbcsql-*.so*' -print | sort | tail -n 1)"
if [[ -z "${driver_library}" ]]; then
  echo "Missing ODBC Driver 18 shared library under ${odbc_root}/lib64" >&2
  exit 1
fi

for runtime_lib in "${runtime_libs[@]}"; do
  if [[ ! -e "${runtime_lib}" ]]; then
    echo "Missing SQL Server client runtime library: ${runtime_lib}" >&2
    exit 1
  fi
done

staging_path="$(mktemp -d)"
cleanup() {
  rm -rf -- "${staging_path}"
}
trap cleanup EXIT

mkdir -p "${staging_path}/etc" "${staging_path}/lib" "${staging_path}/msodbcsql18"
cp -a "${tools_root}/." "${staging_path}/"
cp -a "${odbc_root}/." "${staging_path}/msodbcsql18/"

for runtime_lib in "${runtime_libs[@]}"; do
  cp -L "${runtime_lib}" "${staging_path}/lib/$(basename "${runtime_lib}")"
done

driver_name="$(basename "${driver_library}")"
cat >"${staging_path}/etc/odbcinst.ini" <<ODBCINST
[ODBC Driver 18 for SQL Server]
Description=Microsoft ODBC Driver 18 for SQL Server
Driver=/opt/evander/toolchains/mssql-client/msodbcsql18/lib64/${driver_name}
UsageCount=1
ODBCINST

cat >"${staging_path}/env.sh" <<'ENVIRONMENT'
export PATH="/opt/evander/toolchains/mssql-client/bin:$PATH"
export LD_LIBRARY_PATH="/opt/evander/toolchains/mssql-client/lib:/opt/evander/toolchains/mssql-client/msodbcsql18/lib64:${LD_LIBRARY_PATH:-}"
export ODBCSYSINI="/opt/evander/toolchains/mssql-client/etc"
ENVIRONMENT

mkdir -p "$(dirname "${output_path}")"
tar -C "${staging_path}" -czf "${output_path}" .
echo "Built MSSQL client toolchain archive at ${output_path}"
