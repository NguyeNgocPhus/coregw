#!/bin/sh
# Run from any directory. Downloads official, pinned tools into the repository cache.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../.." && pwd)
CACHE="$ROOT/.cache/signoz"
mkdir -p "$CACHE/bin"
if [ ! -x "$CACHE/bin/foundryctl" ]; then
  curl -fsSL https://signoz.io/foundry.sh -o "$CACHE/foundry-install.sh"
  bash "$CACHE/foundry-install.sh" -v v0.3.0 -d "$CACHE/bin" -y
fi
if [ ! -f "$CACHE/opentelemetry-javaagent.jar" ]; then
  curl -fL https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v2.32.0/opentelemetry-javaagent.jar \
    -o "$CACHE/opentelemetry-javaagent.jar.tmp"
  mv "$CACHE/opentelemetry-javaagent.jar.tmp" "$CACHE/opentelemetry-javaagent.jar"
fi
if [ "$(uname -s)" = Darwin ]; then
  "$HERE/start-native-metrics.sh"
fi
cd "$HERE"
"$CACHE/bin/foundryctl" forge -f casting.yaml
docker compose -f "$ROOT/coregw-mvc/compose.redis.yml" --profile monitoring up -d
docker compose -f pours/deployment/compose.yaml up -d
printf '\nSigNoz UI: http://localhost:3301\nJava agent: %s/opentelemetry-javaagent.jar\n' "$CACHE"
