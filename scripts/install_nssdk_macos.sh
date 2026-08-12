#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "NSSDK installer only supports macOS." >&2
  exit 1
fi

sdk_install_root="${1:-${HOME:?}/Library/Application Support/CardPlatform}"
sdk_destination="${sdk_install_root}/nssdk.framework"
sdk_temp_dir="$(mktemp -d -t card-platform-nssdk)"
trap 'rm -rf "${sdk_temp_dir}"' EXIT

curl --fail --location --output "${sdk_temp_dir}/nssdk.zip" \
  "https://pan.scandocs.net/d/1/Software/Camera/Development%20kit/nssdk-macos-universal.zip"
unzip -q "${sdk_temp_dir}/nssdk.zip" \
  'nssdk-macos-universal/lib/nssdk.framework/*' \
  -d "${sdk_temp_dir}/unpacked"
mkdir -p "${sdk_install_root}"
ditto \
  "${sdk_temp_dir}/unpacked/nssdk-macos-universal/lib/nssdk.framework" \
  "${sdk_destination}"

echo "Installed NSSDK to ${sdk_destination}"
