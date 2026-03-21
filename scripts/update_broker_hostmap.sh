#!/usr/bin/env bash
set -euo pipefail

BROKER_HOST="DOMCOM2"
BROKER_PORT="8883"
BROKER_IP=""

usage() {
  cat <<'EOF'
Usage:
  scripts/update_broker_hostmap.sh --broker-ip <IP> [--broker-host DOMCOM2] [--broker-port 8883]

What it does:
  1) Removes existing /etc/hosts entries for broker host
  2) Adds fresh mapping: <IP> <HOST>
  3) Verifies hostname resolution and TCP reachability to broker port

Example:
  scripts/update_broker_hostmap.sh --broker-ip 192.168.1.17 --broker-host DOMCOM2 --broker-port 8883
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --broker-host)
      BROKER_HOST="${2:-}"
      shift 2
      ;;
    --broker-ip)
      BROKER_IP="${2:-}"
      shift 2
      ;;
    --broker-port)
      BROKER_PORT="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1"
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$BROKER_IP" ]]; then
  echo "Error: --broker-ip is required"
  usage
  exit 2
fi

if ! [[ "$BROKER_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "Error: invalid IPv4 address: $BROKER_IP"
  exit 2
fi

tmp_hosts="$(mktemp)"
trap 'rm -f "$tmp_hosts"' EXIT

# Keep all existing entries except lines that include the target host alias.
awk -v host="$BROKER_HOST" '
  BEGIN { IGNORECASE = 1 }
  {
    drop = 0
    for (i = 2; i <= NF; i++) {
      if (tolower($i) == tolower(host)) {
        drop = 1
        break
      }
    }
    if (!drop) print $0
  }
' /etc/hosts > "$tmp_hosts"

printf "%s %s\n" "$BROKER_IP" "$BROKER_HOST" >> "$tmp_hosts"

sudo cp "$tmp_hosts" /etc/hosts

echo "Updated /etc/hosts with: $BROKER_IP $BROKER_HOST"

resolved="$(getent hosts "$BROKER_HOST" | awk '{print $1}' | head -n 1 || true)"
if [[ -z "$resolved" ]]; then
  echo "Resolution check failed for host: $BROKER_HOST"
  exit 1
fi

echo "Resolved $BROKER_HOST -> $resolved"

if nc -vz "$BROKER_HOST" "$BROKER_PORT" >/dev/null 2>&1; then
  echo "TCP check passed: $BROKER_HOST:$BROKER_PORT reachable"
else
  echo "TCP check failed: $BROKER_HOST:$BROKER_PORT not reachable"
  exit 1
fi
