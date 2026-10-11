#!/usr/bin/env python3
"""Run one gateway at a time; same Java executable/heap, with local mock core."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def control(path, delay=200, status=201, disconnect=False):
    temp = path.with_suffix('.tmp')
    temp.write_text(f'delayMs={delay}\nstatus={status}\ndisconnect={str(disconnect).lower()}\n')
    temp.replace(path)


def wait_gateway(process, port):
    for _ in range(300):
        if process.poll() is not None:
            raise RuntimeError('Gateway exited; inspect gateway log')
        try:
            with urllib.request.urlopen(f'http://localhost:{port}/actuator/health', timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(.1)
    raise RuntimeError('Gateway did not become healthy within 30 seconds')


def wait_core(process, port):
    import socket
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError('Mock exited; inspect mock log')
        try:
            with socket.create_connection(('localhost', port), timeout=.2):
                return
        except OSError:
            time.sleep(.1)
    raise RuntimeError('Mock core did not start')


def stop(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', action='store_true', help='Also write raw k6 JSONL samples (large files)')
    parser.add_argument('--suite', choices=['baseline', 'resilience'], default='baseline')
    parser.add_argument('--executor', choices=['vus', 'rate'], default='vus')
    parser.add_argument('--modes', nargs='+', choices=['direct', 'webflux', 'mvc-platform', 'mvc-virtual'],
                        default=['direct', 'webflux', 'mvc-platform', 'mvc-virtual'])
    parser.add_argument('--delays', nargs='+', type=int, default=[50, 200, 1000])
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--warmup', type=int, default=30)
    parser.add_argument('--hold', type=int, default=120)
    parser.add_argument('--ramp', type=int, default=10)
    parser.add_argument('--phase-seconds', type=int, default=30)
    parser.add_argument('--levels', default=None)
    parser.add_argument('--resilience-vus', type=int, default=600)
    parser.add_argument('--java', default=shutil.which('java'))
    parser.add_argument('--javac', default=shutil.which('javac'))
    parser.add_argument('--k6', default=shutil.which('k6'))
    parser.add_argument('--gateway-arg', action='append', default=[], help='Additional Spring argument applied to all gateways')
    parser.add_argument('--heap', default='512m')
    parser.add_argument('--gateway-port', type=int, default=8080)
    parser.add_argument('--core-port', type=int, default=9090)
    args = parser.parse_args()
    if not all([args.java, args.javac, args.k6]):
        parser.error('Install Java 21+ (JDK) and k6, or supply --java/--javac/--k6')
    if min(args.repetitions, args.warmup, args.hold, args.ramp, args.phase_seconds, args.resilience_vus) < 1:
        parser.error('Repetitions/durations/VUs must be positive')
    if any(d < 0 for d in args.delays):
        parser.error('Delays must be >= 0')
    results = HERE / 'results' / time.strftime('%Y%m%d-%H%M%S')
    results.mkdir(parents=True, exist_ok=False)
    classes = ROOT / '.cache' / 'mock-classes'
    classes.mkdir(parents=True, exist_ok=True)
    subprocess.run([args.javac, '--release', '21', '-d', str(classes), str(HERE / 'mock/MockCore.java')], check=True)
    (results / 'environment.json').write_text(json.dumps({
        'arguments': vars(args),
        'java': subprocess.run([args.java, '-version'], capture_output=True, text=True).stderr,
        'k6': subprocess.run([args.k6, 'version'], capture_output=True, text=True).stdout,
        'cpuCount': os.cpu_count(),
    }, indent=2))
    failures = []
    delays = args.delays if args.suite == 'baseline' else [200]
    for delay in delays:
        for repeat in range(1, args.repetitions + 1):
            for mode in args.modes:
                label = f'{mode}-{args.suite}-{delay}ms-run{repeat}'
                print(f'Running {label}', flush=True)
                ctl = results / f'{label}.properties'
                stats = results / f'{label}-core-stats.json'
                control(ctl, delay)
                mock = gateway = load = None
                logs = []
                try:
                    mocklog = (results / f'{label}-mock.log').open('w'); logs.append(mocklog)
                    mock = subprocess.Popen([args.java, f'-Xms{args.heap}', f'-Xmx{args.heap}', '-cp', str(classes), 'MockCore'],
                        env={**os.environ, 'MOCK_PORT': str(args.core_port), 'MOCK_DELAY_MS': str(delay),
                             'MOCK_CONTROL_FILE': str(ctl), 'MOCK_STATS_FILE': str(stats)},
                        stdout=mocklog, stderr=subprocess.STDOUT)
                    wait_core(mock, args.core_port)
                    if mode != 'direct':
                        module = 'coregw-webflux' if mode == 'webflux' else 'coregw-mvc'
                        jar = ROOT / module / 'target' / f'{module}-0.0.1-SNAPSHOT.jar'
                        if not jar.is_file():
                            raise RuntimeError(f'Missing {jar}; run ./mvnw package first')
                        profiles = args.suite if mode == 'webflux' else f'{mode.removeprefix("mvc-")},{args.suite}'
                        # k6 suite name resilience corresponds to Spring profile resilient.
                        profiles = profiles.replace('resilience', 'resilient')
                        gatewaylog = (results / f'{label}-gateway.log').open('w'); logs.append(gatewaylog)
                        gateway = subprocess.Popen([args.java, f'-Xms{args.heap}', f'-Xmx{args.heap}', '-jar', str(jar),
                            f'--spring.profiles.active={profiles}', f'--server.port={args.gateway_port}',
                            f'--gateway.core-url=http://localhost:{args.core_port}', '--gateway.core-path=/orders'] + args.gateway_arg,
                            stdout=gatewaylog, stderr=subprocess.STDOUT)
                        wait_gateway(gateway, args.gateway_port)
                    env = {**os.environ, 'BASE_URL': f'http://localhost:{args.core_port if mode == "direct" else args.gateway_port}',
                        'ORDER_PATH': '/orders' if mode == 'direct' else '/api/orders',
                        'MODE': mode, 'PROFILE': args.suite, 'WORKLOAD': args.suite,
                        'EXECUTOR': args.executor, 'REPETITION': str(repeat), 'RUN_ID': label,
                        'CORE_DELAY_MS': str(delay), 'WARMUP_SECONDS': str(args.warmup),
                        'HOLD_SECONDS': str(args.hold), 'RAMP_SECONDS': str(args.ramp),
                        'RESILIENCE_SECONDS': str(args.phase_seconds * 5), 'RESILIENCE_VUS': str(args.resilience_vus),
                        'RESULT_FILE': str(results / f'{label}.json')}
                    if args.levels:
                        env['LEVELS'] = args.levels
                    loadlog = (results / f'{label}-k6.log').open('w'); logs.append(loadlog)
                    load_command = [args.k6, 'run']
                    if args.samples:
                        load_command += ['--out', f'json={results / (label + "-samples.jsonl")}']
                    load_command.append(str(HERE / 'create-order.js'))
                    load = subprocess.Popen(load_command, cwd=HERE, env=env, stdout=loadlog, stderr=subprocess.STDOUT)
                    # k6 startup overhead is small but nonzero; phase boundaries in samples are approximate.
                    if args.suite == 'resilience':
                        schedule = [(args.warmup + args.phase_seconds, 200, 500, False),
                            (args.warmup + args.phase_seconds * 2, 4000, 201, False),
                            (args.warmup + args.phase_seconds * 3, 0, 201, True),
                            (args.warmup + args.phase_seconds * 4, 200, 201, False)]
                        started = time.monotonic()
                        for at, phase_delay, status, disconnect in schedule:
                            while load.poll() is None and time.monotonic() - started < at:
                                time.sleep(.1)
                            if load.poll() is not None:
                                break
                            control(ctl, phase_delay, status, disconnect)
                    code = load.wait()
                    if code:
                        failures.append({'run': label, 'exitCode': code})
                    if gateway:
                        for metric in ['jvm.memory.used', 'jvm.threads.live', 'process.cpu.usage',
                                       'resilience4j.circuitbreaker.calls', 'resilience4j.bulkhead.available.concurrent.calls']:
                            try:
                                with urllib.request.urlopen(f'http://localhost:{args.gateway_port}/actuator/metrics/{metric}') as response:
                                    (results / f'{label}-{metric}.json').write_bytes(response.read())
                            except OSError:
                                pass
                finally:
                    stop(load); stop(gateway); stop(mock)
                    for log in logs:
                        log.close()
    (results / 'failures.json').write_text(json.dumps(failures, indent=2))
    print(f'Results: {results}')
    if failures:
        raise SystemExit('Some k6 runs failed/violated thresholds; see failures.json and logs')


if __name__ == '__main__':
    main()
