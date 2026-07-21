#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
plain_file="$repo_dir/.env"
encrypted_file="$repo_dir/.env.age"

require_age() {
  if ! command -v age >/dev/null 2>&1; then
    echo "未找到 age。macOS 可运行: brew install age" >&2
    exit 1
  fi
}

encrypt() {
  require_age
  if [[ ! -f "$plain_file" ]]; then
    echo "未找到 $plain_file，请先从 .env.example 创建并配置。" >&2
    exit 1
  fi

  local temporary_file
  temporary_file="$(mktemp "$repo_dir/.env.age.tmp.XXXXXX")"
  trap 'rm -f "$temporary_file"' EXIT
  echo "请输入用于跨机器解密的强密码（密码不会写入仓库）："
  age --passphrase --output "$temporary_file" "$plain_file"
  chmod 600 "$temporary_file"
  mv "$temporary_file" "$encrypted_file"
  trap - EXIT
  echo "已生成 $encrypted_file；可安全提交该密文文件。"
}

decrypt() {
  require_age
  if [[ ! -f "$encrypted_file" ]]; then
    echo "未找到 $encrypted_file。" >&2
    exit 1
  fi
  if [[ -e "$plain_file" ]]; then
    echo "$plain_file 已存在，为防止覆盖已停止。请先备份或移走它。" >&2
    exit 1
  fi

  local temporary_file
  temporary_file="$(mktemp "$repo_dir/.env.tmp.XXXXXX")"
  trap 'rm -f "$temporary_file"' EXIT
  age --decrypt --output "$temporary_file" "$encrypted_file"
  chmod 600 "$temporary_file"
  mv "$temporary_file" "$plain_file"
  trap - EXIT
  echo "已恢复 $plain_file（该文件仍被 Git 忽略）。"
}

case "${1:-}" in
  encrypt) encrypt ;;
  decrypt) decrypt ;;
  *)
    echo "用法: $0 encrypt|decrypt" >&2
    exit 2
    ;;
esac
