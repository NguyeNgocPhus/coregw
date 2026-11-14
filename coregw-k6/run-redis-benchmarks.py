#!/usr/bin/env python3
"""Run Redis comparisons inside coregw-mvc against an already-running dedicated Redis."""
import argparse
import itertools
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import time
from importlib.machinery import SourceFileLoader
from redis_report import write_report

HERE = Path(__file__).resolve().parent
helpers = SourceFileLoader('gateway_runner', str(HERE / 'run-benchmarks.py')).load_module()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--connections', type=int, nargs='+', default=[1])
    p.add_argument('--rate', type=int, default=50, help='HTTP requests per second')
    p.add_argument('--payload-bytes', type=int, default=1024)
    p.add_argument('--keys', type=int, default=10000)
    p.add_argument('--warmup', type=int, default=10)
    p.add_argument('--hold', type=int, default=60)
    p.add_argument('--repetitions', type=int, default=1)
    p.add_argument('--thread-model', choices=['virtual', 'platform'], default='virtual')
    p.add_argument('--port', type=int, default=8080)
    p.add_argument('--java', default=shutil.which('java'))
    p.add_argument('--k6', default=shutil.which('k6'))
    p.add_argument('--seed', type=int, default=42)
    a = p.parse_args()
    if not a.java or not a.k6:
        p.error('Java and k6 are required')
    if min(a.connections + [a.keys, a.payload_bytes, a.warmup, a.hold, a.repetitions]) < 1:
        p.error('All matrix values must be positive')
    if a.rate is not None and a.rate < 1:
        p.error('Rate must be positive')
    jar = HERE.parent / 'coregw-mvc/target/coregw-mvc-0.0.1-SNAPSHOT.jar'
    if not jar.exists():
        p.error('Run ./mvnw -pl coregw-mvc -am package first')
    result = HERE / 'results' / ('redis-' + time.strftime('%Y%m%d-%H%M%S'))
    result.mkdir(parents=True)
    (result / 'environment.json').write_text(json.dumps({
        'arguments': vars(a), 'java': subprocess.run([a.java, '-version'], capture_output=True, text=True).stderr,
        'k6': subprocess.run([a.k6, 'version'], capture_output=True, text=True).stdout,
        'cpuCount': os.cpu_count(), 'redis': 'Externally managed; record Redis version/config and host resources separately',
    }, indent=2))
    matrix = list(itertools.product(range(1, a.repetitions + 1), ['multiplexing', 'pool'], a.connections))
    matrix = [run for run in matrix if run[1] == "pool" or run[2] == 1]
    random.Random(a.seed).shuffle(matrix)
    failures = []
    for repeat, mode, connections in matrix:
        label = f'{mode}-c{connections}-r{repeat}'
        print(label, flush=True)
        gateway = load = None
        try:
            with (result / f'{label}-gateway.log').open('w') as log:
                gateway = subprocess.Popen([a.java, '-Xms512m', '-Xmx512m', '-jar', str(jar),
                    f'--spring.profiles.active={a.thread_model},baseline,redis-bench', f'--server.port={a.port}',
                    f'--app.redis.mode={mode}', f'--app.redis.pool.max-active={connections}',
                    f'--app.redis.pool.max-idle={connections}',
                    f'--redis-bench.payload-bytes={a.payload_bytes}', f'--redis-bench.keys={a.keys}'],
                    stdout=log, stderr=subprocess.STDOUT)
                helpers.wait_gateway(gateway, a.port)
                env = {**os.environ, 'BASE_URL': f'http://localhost:{a.port}', 'MODE': mode,
                    'CONNECTIONS': str(connections),
                    'KEYS': str(a.keys), 'PAYLOAD_BYTES': str(a.payload_bytes),
                    'WARMUP_SECONDS': str(a.warmup), 'HOLD_SECONDS': str(a.hold),
                    'RESULT_FILE': str(result / f'{label}.json')}
                env.pop('RATE', None)
                if a.rate:
                    env['RATE'] = str(a.rate)
                with (result / f'{label}-k6.log').open('w') as loadlog:
                    load = subprocess.Popen([a.k6, 'run', str(HERE / 'redis.js')], env=env,
                                            stdout=loadlog, stderr=subprocess.STDOUT)
                    if load.wait():
                        failures.append(label)
        finally:
            helpers.stop(load)
            helpers.stop(gateway)
    (result / 'failures.json').write_text(json.dumps(failures))
    write_report(result)
    print(f'Report: {result / "report.html"}')
    if failures:
        raise SystemExit('Some runs failed thresholds; inspect failures.json')


if __name__ == '__main__':
    main()
