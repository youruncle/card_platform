#!/bin/zsh

set -euo pipefail

script_dir=${0:A:h}
repo_dir=${script_dir:h}
runtime_dir="$repo_dir/work/runtime"
launchd_domain="gui/$(id -u)"
launch_agents_dir="/Users/song/Library/LaunchAgents"
logs_dir="/Users/song/Library/Logs/CardPlatform"
api_label="com.cardplatform.api"
web_label="com.cardplatform.web"

api_health() {
  curl --fail --silent --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1
}

web_health() {
  curl --fail --silent --max-time 2 http://127.0.0.1:3000/ >/dev/null 2>&1
}

service_loaded() {
  launchctl print "$launchd_domain/$1" >/dev/null 2>&1
}

install_service() {
  local label=$1
  local template=$2
  local installed="$launch_agents_dir/$label.plist"
  local changed=false
  mkdir -p "$runtime_dir" "$launch_agents_dir" "$logs_dir"
  if [[ ! -f "$installed" ]] || ! cmp -s "$template" "$installed"; then
    /usr/bin/install -m 0644 "$template" "$installed"
    changed=true
  fi
  if [[ "$changed" == true ]] && service_loaded "$label"; then
    launchctl bootout "$launchd_domain/$label"
  fi
  if ! service_loaded "$label"; then
    launchctl bootstrap "$launchd_domain" "$installed"
  fi
}

install_services() {
  install_service "$api_label" "$repo_dir/scripts/launchd/$api_label.plist"
  install_service "$web_label" "$repo_dir/scripts/launchd/$web_label.plist"
}

wait_until_healthy() {
  local service_name=$1
  local health_function=$2
  local attempt
  local consecutive_successes=0
  for attempt in {1..40}; do
    if "$health_function"; then
      ((consecutive_successes += 1))
      if (( consecutive_successes >= 3 )); then
        print "$service_name 已就绪"
        return 0
      fi
    else
      consecutive_successes=0
    fi
    sleep 0.5
  done
  print -u2 "$service_name 未通过持续健康检查"
  return 1
}

start_services() {
  install_services
  if ! api_health; then
    launchctl kickstart -k "$launchd_domain/$api_label"
  fi
  wait_until_healthy "API" api_health
  if ! web_health; then
    launchctl kickstart -k "$launchd_domain/$web_label"
  fi
  wait_until_healthy "Web" web_health
}

stop_service() {
  local label=$1
  if service_loaded "$label"; then
    launchctl bootout "$launchd_domain/$label"
    print "$label 已停止"
  else
    print "$label 未运行"
  fi
}

status_services() {
  if api_health; then
    print "API: healthy"
  else
    print "API: unavailable"
  fi
  if web_health; then
    print "Web: healthy"
  else
    print "Web: unavailable"
  fi
  for label in "$api_label" "$web_label"; do
    if service_loaded "$label"; then
      print "$label: loaded"
    else
      print "$label: not loaded"
    fi
  done
}

case ${1:-start} in
  install|start)
    start_services
    ;;
  restart-api)
    install_services
    launchctl kickstart -k "$launchd_domain/$api_label"
    wait_until_healthy "API" api_health
    ;;
  restart-web)
    install_services
    launchctl kickstart -k "$launchd_domain/$web_label"
    wait_until_healthy "Web" web_health
    ;;
  stop)
    stop_service "$web_label"
    stop_service "$api_label"
    ;;
  status)
    status_services
    ;;
  *)
    print -u2 "用法：$0 {install|start|restart-api|restart-web|stop|status}"
    exit 2
    ;;
esac
