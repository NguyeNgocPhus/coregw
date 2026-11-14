#!/usr/bin/env python3
"""macOS process RSS/context switches and optional HotSpot NMT, without app dependencies."""
import argparse
import ctypes
import json
import logging
from pathlib import Path
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class TaskInfo(ctypes.Structure):
    # Darwin SDK sys/proc_info.h: struct proc_taskinfo, PROC_PIDTASKINFO=4.
    _fields_ = [(n, ctypes.c_uint64) for n in
                ('virtual_size', 'resident_size', 'total_user', 'total_system', 'threads_user', 'threads_system')]
    _fields_ += [(n, ctypes.c_int32) for n in
                 ('policy', 'faults', 'pageins', 'cow_faults', 'messages_sent', 'messages_received',
                  'syscalls_mach', 'syscalls_unix', 'csw', 'threadnum', 'numrunning', 'priority')]


def task_info(pid):
    lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.restype = ctypes.c_int
    info = TaskInfo()
    if lib.proc_pidinfo(pid, 4, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
        raise OSError(ctypes.get_errno(), 'proc_pidinfo failed')
    return info


def parse_nmt(text):
    thread = re.search(r'^\s*-\s+Thread \(reserved=(\d+)KB, committed=(\d+)KB\)', text, re.M)
    stack = re.search(r'\(stack: reserved=(\d+)KB, committed=(\d+)KB(?:, [^)]*)?\)', text)
    if not thread or not stack:
        return None
    return dict(zip(('thread_reserved', 'thread_committed', 'stack_reserved', 'stack_committed'),
                    (int(v) * 1024 for v in (*thread.groups(), *stack.groups()))))


def identify_service(command, requested='auto'):
    # Main class is authoritative; classpaths may contain both module names.
    if 'vn.coregw.GatewayWebFluxApplication' in command:
        found = 'coregw-webflux'
    elif 'vn.coregw.GatewayApplication' in command:
        found = 'coregw-webflux' if '/coregw-webflux/' in command else 'coregw-mvc'
    elif 'coregw-webflux-' in command and '.jar' in command:
        found = 'coregw-webflux'
    elif 'coregw-mvc-' in command and '.jar' in command:
        found = 'coregw-mvc'
    else:
        return None
    return found if requested in ('auto', found) else None


def discover(port, service):
    p = subprocess.run(['lsof', '-nP', '-t', f'-iTCP:{port}', '-sTCP:LISTEN'], capture_output=True, text=True, timeout=3)
    for pid in set(p.stdout.split()):
        command = subprocess.check_output(['ps', '-p', pid, '-o', 'command='], text=True, timeout=3)
        found = identify_service(command, service)
        if found and 'java' in command:
            return int(pid), command.strip().split()[0], found
    return None, None, None


def write_discovery(directory, service, port):
    directory.mkdir(parents=True, exist_ok=True)
    targets = [] if service is None else [{'targets': [f'host.docker.internal:{port}'],
                                          'labels': {'application': service}}]
    path = directory / 'app-targets.json'
    content = json.dumps(targets)
    if not path.exists() or path.read_text() != content:
        temporary = path.with_suffix('.tmp')
        temporary.write_text(content)
        temporary.replace(path)


class Collector:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.text = ''
        self.stop = threading.Event()
        self.pid = None
        self.nmt = None
        self.nmt_time = 0
        self.nmt_next = 0

    def sample(self):
        pid, java, service = discover(self.args.target_port, self.args.service)
        write_discovery(self.args.discovery_dir, service, self.args.target_port)
        if pid != self.pid:
            self.pid, self.nmt, self.nmt_time, self.nmt_next = pid, None, 0, 0
        labels = {'environment': 'local', 'application': service or 'unknown'}
        if pid:
            labels['process_pid'] = str(pid)
        label = '{' + ','.join(k + '=' + json.dumps(v) for k, v in labels.items()) + '}'
        lines = []

        def emit(name, value, kind='gauge'):
            lines.extend([f'# TYPE {name} {kind}', f'{name}{label} {value}'])

        emit('coregw_native_target_up', int(pid is not None))
        if pid:
            info = task_info(pid)
            emit('coregw_process_resident_memory_bytes', info.resident_size)
            emit('coregw_process_context_switches_total', info.csw & 0xffffffff, 'counter')
            emit('coregw_process_os_threads', info.threadnum)
            if time.monotonic() >= self.nmt_next:
                self.nmt = None
                try:
                    result = subprocess.run([str(Path(java).parent / 'jcmd'), str(pid), 'VM.native_memory', 'summary', 'scale=KB'],
                                            text=True, capture_output=True, timeout=5)
                    self.nmt = parse_nmt(result.stdout) if result.returncode == 0 else None
                    if self.nmt:
                        self.nmt_time = time.time()
                except (OSError, subprocess.TimeoutExpired):
                    logging.warning('NMT sample failed for pid %s', pid)
                self.nmt_next = time.monotonic() + 30
            emit('coregw_nmt_available', int(self.nmt is not None))
            if self.nmt:
                for key, value in self.nmt.items():
                    emit('coregw_nmt_' + key + '_bytes', value)
                emit('coregw_nmt_sample_timestamp_seconds', self.nmt_time)
        with self.lock:
            self.text = '\n'.join(lines) + '\n'

    def run(self):
        while not self.stop.is_set():
            try:
                self.sample()
            except Exception:
                logging.exception('Native sample failed')
                write_discovery(self.args.discovery_dir, None, self.args.target_port)
                with self.lock:
                    self.text = 'coregw_native_target_up{environment="local"} 0\n'
            self.stop.wait(5)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target-port', type=int, default=8080)
    p.add_argument('--service', default='auto', choices=['auto', 'coregw-mvc', 'coregw-webflux'])
    p.add_argument('--discovery-dir', type=Path, default=Path(__file__).resolve().parents[3] / '.cache/signoz/discovery')
    p.add_argument('--port', type=int, default=9257)
    args = p.parse_args()
    collector = Collector(args)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/metrics':
                self.send_error(404)
                return
            with collector.lock:
                body = collector.text.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    threading.Thread(target=collector.run, daemon=True).start()
    logging.warning('Native exporter on 127.0.0.1:%s; target %s:%s', args.port, args.service, args.target_port)
    try:
        server.serve_forever()
    finally:
        collector.stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
