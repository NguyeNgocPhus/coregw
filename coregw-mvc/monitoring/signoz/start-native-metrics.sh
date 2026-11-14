#!/bin/sh
# Host-side exporter: Darwin libproc is unavailable inside Linux Docker containers.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$HERE/../../.." && pwd)
python3 - "$HERE" "$ROOT" <<'PY'
import os
from pathlib import Path
import subprocess
import sys
here, root = map(Path, sys.argv[1:])
cache = root / '.cache/signoz'
cache.mkdir(parents=True, exist_ok=True)
pid_file = cache / 'native-metrics.pid'
if pid_file.exists():
    try:
        pid = int(pid_file.read_text())
        command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='], text=True)
        if str(here / 'native_metrics.py') in command:
            print('Native metrics already running, PID', pid)
            sys.exit(0)
    except (ValueError, subprocess.CalledProcessError):
        pass
with (cache / 'native-metrics.log').open('a') as log:
    proc = subprocess.Popen([sys.executable, str(here / 'native_metrics.py')],
                            stdout=log, stderr=log, start_new_session=True)
pid_file.write_text(str(proc.pid))
print('Native metrics started, PID', proc.pid)
PY
