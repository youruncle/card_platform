#!/bin/zsh

set -euo pipefail

repo_dir="/Users/song/Documents/Codex/2026-07-19/w/card_platform"
web_dir="$repo_dir/apps/web"
standalone_dir="$web_dir/.next/standalone"

if [[ ! -f "$standalone_dir/server.js" ]]; then
  print -u2 "缺少 Next.js standalone 构建：$standalone_dir/server.js"
  exit 1
fi

ln -sfn "$web_dir/.next/static" "$standalone_dir/.next/static"
if [[ -d "$web_dir/public" ]]; then
  ln -sfn "$web_dir/public" "$standalone_dir/public"
fi

cd "$standalone_dir"
exec /Users/song/.local/node/node-v22.21.1/bin/node server.js
