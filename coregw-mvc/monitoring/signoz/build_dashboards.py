#!/usr/bin/env python3
"""Build SigNoz v6 dashboards for the existing Micrometer/Prometheus data source.

Reference: https://signoz.io/docs/dashboards/dashboard-templates/jvm-metrics/
Agent OTel JVM names differ: see DASHBOARDS.md for the explicit metric mapping.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
GROUP = '"service.name","service.instance.id",redis_mode'
BASE = '"service.name"=~"$service","service.instance.id"=~"$instance"'
APP = BASE + ',redis_mode=~"$mode",environment=~"$environment"'
REDIS = '"service.name"="redis","service.instance.id"=~"$redis_instance"'


def metric(name, extra='', scope=APP):
    # The OTel Prometheus receiver stores histogram/summary suffixes as .bucket/.count/.sum.
    if name.startswith('coregw_'):
        extra = ','.join(x for x in [extra, 'telemetry_source="native_os"'] if x)
    return '{__name__=' + json.dumps(name) + ',' + ','.join(x for x in [scope, extra] if x) + '}'


def rate(name, extra='', scope=APP):
    return f'rate({metric(name, extra, scope)}[1m])'


def total(expr, group=GROUP):
    return f'sum by ({group}) ({expr})'


def quantile(name, percentile, extra='', group=GROUP):
    return f'histogram_quantile({percentile}, sum by (le,{group}) ({rate(name + ".bucket", extra)}))'


def union(*curves):
    """One SigNoz PromQL query per panel, with short, explicit series labels."""
    return ' or '.join(f'label_replace(({expr}), "view", {json.dumps(label)}, "__name__", ".*")'
                       for label, expr in curves)


def variable(name, label, attribute=None, default='', values=None, allow_all=True):
    if values:
        plugin = {'kind': 'signoz/CustomVariable', 'spec': {'customValue': ','.join(values)}}
    else:
        plugin = {'kind': 'signoz/DynamicVariable', 'spec': {'name': attribute or name, 'signal': 'metrics'}}
    spec = {'name': name, 'display': {'name': label, 'description': ''},
            'allowAllValue': allow_all, 'allowMultiple': allow_all, 'customAllValue': '.*' if allow_all else '',
            'capturingRegexp': '', 'sort': 'alphabetical-asc', 'plugin': plugin}
    if default:
        spec['defaultValue'] = default
    return {'kind': 'ListVariable', 'spec': spec}


APP_VARIABLES = [variable('service', 'Service', values=['coregw-mvc', 'coregw-webflux']),
                 variable('instance', 'JVM instance', 'service.instance.id'),
                 variable('mode', 'Redis mode', 'redis_mode'),
                 variable('environment', 'Environment', 'environment')]


class Dashboard:
    def __init__(self, title, description, variables):
        self.doc = {'schemaVersion': 'v6', 'generateName': True,
                    'tags': [{'key': 'tag', 'value': 'coregw'}, {'key': 'tag', 'value': 'operations'}],
                    'spec': {'display': {'name': title, 'description': description}, 'variables': variables,
                             'panels': {}, 'layouts': [], 'duration': '30m', 'refreshInterval': '5s', 'links': []}}

    def section(self, title):
        self.layout = {'kind': 'Grid', 'spec': {'display': {'title': title, 'collapse': {'open': True}}, 'items': []}}
        self.doc['spec']['layouts'].append(self.layout)
        self.x = self.y = 0

    def panel(self, key, title, expr, unit='short', description='', width=6, height=5, number=False,
              legend='', thresholds=()):
        if self.x + width > 12:
            self.x = 0
            self.y += self.row_height
        if self.x == 0:
            self.row_height = height
        self.row_height = max(self.row_height, height)
        spec = {'visualization': {'timePreference': 'global_time'},
                'formatting': {'unit': unit, 'decimalPrecision': '2'}, 'thresholds': None}
        if not number:
            spec.update({'chartAppearance': {'lineInterpolation': 'linear', 'showPoints': False, 'lineStyle': 'solid',
                                             'fillMode': 'none', 'spanGaps': {'fillOnlyBelow': False, 'fillLessThan': ''}},
                         'axes': {'softMin': 0, 'softMax': None, 'isLogScale': False},
                         'legend': {'position': 'bottom', 'mode': 'list', 'customColors': None}})
        if thresholds:
            spec['thresholds'] = [({'value': value, 'operator': 'above', 'unit': unit, 'color': color, 'format': 'text'} if number
                                   else {'value': value, 'unit': unit, 'color': color, 'label': label})
                                  for value, color, label in thresholds]
        self.doc['spec']['panels'][key] = {'kind': 'Panel', 'spec': {
            'display': {'name': title, 'description': description},
            'plugin': {'kind': 'signoz/NumberPanel' if number else 'signoz/TimeSeriesPanel', 'spec': spec},
            'queries': [{'kind': 'scalar' if number else 'time_series', 'spec': {'name': 'A', 'plugin': {
                'kind': 'signoz/PromQLQuery', 'spec': {'name': 'A', 'query': expr, 'legend': legend, 'disabled': False}}}}],
            'links': []}}
        self.layout['spec']['items'].append({'x': self.x, 'y': self.y, 'width': width, 'height': height,
                                            'content': {'$ref': '#/spec/panels/' + key}})
        self.x += width

    def write(self, filename):
        (HERE / filename).write_text(json.dumps(self.doc, indent=2, ensure_ascii=False) + '\n')


def build_jvm():
    d = Dashboard('Coregw — MVC / WebFlux · JVM & HTTP Operations',
                  'JVM template adapted to Micrometer: CPU, memory pools, GC, threads, class loading, HTTP. '
                  'Select one instance for diagnosis. No-data is not zero/healthy. No agent JVM metrics are duplicated.', APP_VARIABLES)
    api = 'uri!~"/actuator.*|/api/bench/redis/prepare|UNKNOWN|NOT_FOUND"'
    calls = total(rate('http_server_requests_seconds.count', api))
    errors = total(rate('http_server_requests_seconds.count', api + ',status=~"5.."'))
    err_ratio = f'(({errors}) or on({GROUP}) (0 * ({calls}))) / clamp_min(({calls}), 1e-9)'
    heap_used = total(metric('jvm_memory_used_bytes', 'area="heap"'))
    heap_max = total(metric('jvm_memory_max_bytes', 'area="heap"') + ' > 0')
    heap_ratio = f'({heap_used}) / ({heap_max})'
    gc_fraction = total(rate('jvm_gc_pause_seconds.sum'))
    p99 = quantile('http_server_requests_seconds', '.99', api)
    d.section('01 · Overview')
    d.panel('status', 'App scrape health', f'min({metric("up", scope=BASE)})', description='1 = reachable, 0 = down. Read together with Sample age; scalar panels can retain the last value in the selected range. Synthetic up has no mode/environment tag.', width=3, height=3, number=True)
    d.panel('sample-age', 'Telemetry sample age', f'max(time() - timestamp({metric("up", scope=BASE)}))', 's', 'Age of the newest scrape sample per target, worst selected target. Investigate values above 20s at a 5s scrape interval. A stopped collector can otherwise leave stale scalar values visible.', 3, 3, True, thresholds=[(20, '#F59E0B', 'Telemetry delay'), (60, '#EF4444', 'Telemetry stale')])
    d.panel('instance-count', 'Scrape target count', f'count({metric("up", scope=BASE)})', 'short', 'Targets represented by scrape health series; includes down targets. Check Sample age and do not interpret as healthy JVM count.', 3, 3, True)
    d.panel('cpu-now', 'CPU usage · max', f'max({metric("process_cpu_usage")})', 'percentunit', 'Fraction of CPU capacity visible to JVM. This is not percent of one core.', 3, 3, True, thresholds=[(.8, '#F59E0B', 'Review sustained CPU')])
    d.panel('heap-now', 'Heap utilization', f'max({heap_ratio})', 'percentunit', 'Sum heap used per JVM divided by sum positive heap limits; ignores -1 undefined pool limits. Does not measure process RSS.', 3, 3, True, thresholds=[(.8, '#F59E0B', 'Review heap pressure')])
    d.panel('gc-now', 'GC pause fraction', f'max({gc_fraction})', 'percentunit', 'GC pause seconds per wall second, worst selected JVM; rate window applies.', 3, 3, True, thresholds=[(.05, '#F59E0B', 'Investigate sustained pauses')])
    d.panel('rps-now', 'HTTP requests/s', f'sum({calls})', 'reqps', 'Total selected traffic, excludes actuator and benchmark prepare. Zero traffic is not an availability result.', 3, 3, True)
    d.panel('p99-now', 'HTTP p99 · max', f'max({p99})', 's', 'Server-side histogram estimate. No requests/histogram means no percentile. End-to-end latency remains in k6.', 3, 3, True)

    d.section('02 · HTTP workload — latency, throughput, errors, endpoints')
    d.panel('http-rate', 'HTTP throughput by JVM', calls, 'reqps', 'Excludes scrape/prepare traffic. Grouped per instance and Redis mode.', legend='{{service.instance.id}} · {{redis_mode}}')
    d.panel('http-errors', 'HTTP 5xx ratio', err_ratio, 'percentunit', 'HTTP 5xx / all business responses. A missing error series becomes zero only if a total series exists. Transport failures before the server are visible in k6, not here.', legend='{{service.instance.id}} · {{redis_mode}}', thresholds=[(.01, '#F59E0B', 'Example: 1% errors; tune to your SLO')])
    d.panel('http-latency', 'HTTP latency · p50 / p90 / p99', union(*[(p, quantile('http_server_requests_seconds', n, api)) for p, n in [('p50', '.5'), ('p90', '.9'), ('p99', '.99')]]), 's', 'Percentiles from buckets, never average percentile values across JVMs. Each JVM is plotted separately.', legend='{{view}} · {{service.instance.id}} · {{redis_mode}}')
    d.panel('http-endpoints', 'HTTP p99 by endpoint', quantile('http_server_requests_seconds', '.99', api, GROUP + ',uri,method'), 's', 'Separate route/method series help identify the slow endpoint. A route with no samples has no percentile.', legend='{{method}} {{uri}} · {{service.instance.id}}')

    d.section('Tomcat · request worker capacity')
    busy = metric('tomcat_threads_busy_threads')
    maximum = metric('tomcat_threads_config_max_threads')
    d.panel('tomcat-threads', 'Tomcat threads · busy / current / max', union(('busy', busy), ('current', metric('tomcat_threads_current_threads')), ('max', maximum)), 'short', 'Platform request workers by connector. Requires server.tomcat.mbeanregistry.enabled=true and application restart. Busy includes I/O wait, not just CPU work. Virtual-thread executors require separate interpretation.', legend='{{view}} · {{service.instance.id}} · {{name}}')
    d.panel('tomcat-utilization', 'Tomcat worker utilization · busy / max', busy + ' / (' + maximum + ' > 0)', 'percentunit', 'Sustained near 100% indicates worker saturation. This is not queue length or queue wait time. Missing series is not zero.', legend='{{service.instance.id}} · {{name}}', thresholds=[(.8, '#F59E0B', '80% workers busy'), (.95, '#EF4444', '95% workers busy')])
    d.panel('tomcat-connections', 'Tomcat connections · current / keepalive / max', union(('current', metric('tomcat_connections_current_connections')), ('keepalive', metric('tomcat_connections_keepalive_current_connections')), ('max', metric('tomcat_connections_config_max_connections'))), 'short', 'Open connections include idle keepalive; connections minus busy threads is not queue length.', legend='{{view}} · {{service.instance.id}} · {{name}}')

    d.section('WebFlux · core connection pool')
    d.panel('netty-pool', 'WebFlux core connections · active / idle / max', union(('active', metric('reactor_netty_connection_provider_active_connections')), ('idle', metric('reactor_netty_connection_provider_idle_connections')), ('max', metric('reactor_netty_connection_provider_max_connections'))), 'short', 'WebFlux outbound core connection pool. No series under MVC is expected. Connections are not event-loop threads.', legend='{{view}} · {{service.instance.id}} · {{name}}')
    d.panel('netty-pending', 'WebFlux core connections · pending / pending limit', union(('pending', metric('reactor_netty_connection_provider_pending_connections')), ('pending limit', metric('reactor_netty_connection_provider_max_pending_connections'))), 'short', 'Requests waiting to acquire an outbound core connection; not Tomcat worker queue length. No series under MVC is expected.', legend='{{view}} · {{service.instance.id}} · {{name}}')

    d.section('03 · JVM CPU & process lifecycle')
    d.panel('cpu-history', 'Process CPU vs host CPU', union(('process', metric('process_cpu_usage')), ('host', metric('system_cpu_usage'))), 'percentunit', 'Both are capacity fractions; host includes Docker, SigNoz, k6 and other applications.', legend='{{view}} · {{service.instance.id}} · {{redis_mode}}')
    d.panel('cpu-cores', 'JVM CPU consumption · core equivalents', rate('process_cpu_time_ns_total') + ' / 1e9', 'short', 'CPU nanoseconds/s divided by 1e9. 1.0 = one CPU core busy. This differs from process CPU capacity percent.', legend='{{service.instance.id}} · {{redis_mode}}')
    d.panel('cpu-count', 'Available processors · minimum selected', f'min({metric("system_cpu_count")})', 'short', 'Processors visible to JVM. Inspect one instance for exact capacity.', 3, 3, True)
    d.panel('uptime', 'Uptime · youngest selected JVM', f'min({metric("process_uptime_seconds")})', 's', 'Low value shows a recent process restart; not host uptime.', 3, 3, True)
    d.panel('host-load', 'Host 1-minute load / visible CPUs', metric('system_load_average_1m') + ' / ' + metric('system_cpu_count'), 'short', 'Load average normalized by CPU count. 1.0 roughly means as many runnable entities as CPUs; not a CPU percentage.', legend='{{service.instance.id}}')

    d.section('04 · JVM memory — heap, non-heap, pools & off-heap buffers')
    d.panel('heap-bytes', 'Heap used / committed / max', union(('used', heap_used), ('committed', total(metric('jvm_memory_committed_bytes', 'area="heap"'))), ('max', heap_max)), 'bytes', 'Committed is JVM-owned capacity, max is its configured ceiling; neither is process RSS. Undefined per-pool limits are excluded.', legend='{{view}} · {{service.instance.id}} · {{redis_mode}}')
    d.panel('heap-pressure', 'Heap utilization & post-GC occupancy', union(('heap used/max', heap_ratio), ('after-GC long-lived occupancy', metric('jvm_memory_usage_after_gc'))), 'percentunit', 'Persistent high after-GC occupancy is more useful than transient Eden spikes. Values have different denominators; labels distinguish them.', legend='{{view}} · {{service.instance.id}} · {{redis_mode}}', thresholds=[(.8, '#F59E0B', 'Investigate sustained occupancy')])
    d.panel('heap-pools', 'Heap pools · Old / Eden / Survivor', metric('jvm_memory_used_bytes', 'area="heap"'), 'bytes', 'Actual pool names come from the running collector. G1 exposes Old Gen, Eden, Survivor; other collectors have different pools.', legend='{{id}} · {{service.instance.id}}')
    d.panel('nonheap', 'Non-heap pools · Metaspace / code / classes', metric('jvm_memory_used_bytes', 'area="nonheap"'), 'bytes', 'Metaspace, compressed class space and code cache. Not heap and not total process RSS.', legend='{{id}} · {{service.instance.id}}')
    d.panel('buffers', 'Direct / mapped buffer memory', metric('jvm_buffer_memory_used_bytes'), 'bytes', 'JVM buffer-pool accounting; does not include all native allocations.', legend='{{id}} · {{service.instance.id}}')
    d.panel('buffer-count', 'Direct / mapped buffer count', metric('jvm_buffer_count_buffers'), 'short', 'Buffer counts should be read alongside bytes. Idle stable buffers are not a leak.', legend='{{id}} · {{service.instance.id}}')

    d.section('Native process memory & scheduling · macOS')
    d.panel('process-rss', 'Process resident memory · RSS', metric('coregw_process_resident_memory_bytes'), 'bytes', 'Darwin proc_pidinfo resident bytes, includes resident heap and native memory. Not equal to NMT committed. Native exporter follows MVC or WebFlux on port 8080 and labels process_pid.', legend='{{service.instance.id}} · pid {{process_pid}}')
    d.panel('context-switches', 'Process context switches / second', rate('coregw_process_context_switches_total'), 'ops', 'Darwin process context switches, all native threads; not only Tomcat. Does not measure time spent switching. Counter is 32-bit on macOS and can wrap; rate handles resets.', legend='{{service.instance.id}} · pid {{process_pid}}')
    d.panel('native-stacks', 'Native thread stacks · reserved / committed', union(('reserved', metric('coregw_nmt_stack_reserved_bytes')), ('committed', metric('coregw_nmt_stack_committed_bytes'))), 'bytes', 'Requires JVM -XX:NativeMemoryTracking=summary at startup. Reserved is address space, committed is JVM accounting, neither is RSS. NMT sampled every 30s. Includes JVM native threads, not only Tomcat; not virtual-thread heap stacks.', legend='{{view}} · pid {{process_pid}}')
    d.panel('native-threads-memory', 'NMT Thread category · reserved / committed', union(('reserved', metric('coregw_nmt_thread_reserved_bytes')), ('committed', metric('coregw_nmt_thread_committed_bytes'))), 'bytes', 'NMT Thread category includes stacks and thread bookkeeping. It is a superset of stack bytes: do not add them.', legend='{{view}} · pid {{process_pid}}')
    d.panel('native-health', 'Native metrics · target / NMT available', union(('target available', metric('coregw_native_target_up')), ('NMT available', metric('coregw_nmt_available'))), 'short', 'Target=1 means matching MVC/WebFlux JVM discovered; NMT=0 means disabled or last sample failed, not zero native memory. Exporter must be running on host port 9257.', legend='{{view}} · pid {{process_pid}}')
    d.panel('nmt-age', 'Native memory sample age', 'time() - ' + metric('coregw_nmt_sample_timestamp_seconds'), 's', 'NMT polls every 30s; absent when unavailable. Native exporter does not invoke jcmd on the HTTP scrape thread.', legend='pid {{process_pid}}')

    d.section('05 · Garbage collection — cost, frequency & pause distribution')
    d.panel('gc-allocation', 'Heap allocation / promotion rate', union(('allocated', total(rate('jvm_gc_memory_allocated_bytes_total'))), ('promoted', total(rate('jvm_gc_memory_promoted_bytes_total')))), 'Bps', 'Counters update around GC. Short intervals can look bursty; use the same rate window for both benchmark modes.', legend='{{view}} · {{service.instance.id}} · {{redis_mode}}')
    d.panel('gc-pause-cost', 'GC pause fraction', gc_fraction, 'percentunit', 'Total pause time per second; shows impact rather than only event duration. Does not represent concurrent GC CPU.', legend='{{service.instance.id}} · {{redis_mode}}', thresholds=[(.05, '#F59E0B', 'Example: sustained 5% pause time')])
    d.panel('gc-frequency', 'GC events by collector / cause', total(rate('jvm_gc_pause_seconds.count'), GROUP + ',gc,cause,action'), 'ops', 'Collection events/s grouped by JVM GC labels. Major/minor interpretation depends on the collector.', legend='{{gc}} · {{cause}} · {{service.instance.id}}')
    mean = total(rate('jvm_gc_pause_seconds.sum'), GROUP + ',gc') + ' / ' + total(rate('jvm_gc_pause_seconds.count'), GROUP + ',gc')
    d.panel('gc-duration', 'GC pauses · mean / p90 / p99', union(('mean', mean), ('p90', quantile('jvm_gc_pause_seconds', '.9', group=GROUP + ',gc')), ('p99', quantile('jvm_gc_pause_seconds', '.99', group=GROUP + ',gc'))), 's', 'GC histogram requires app restart after jvm.gc.pause histogram is enabled. Until then only mean exists. No GC in the chosen window means no duration, not 0 ms.', legend='{{view}} · {{gc}} · {{service.instance.id}}')

    d.section('06 · Threads, classes & process resources')
    d.panel('threads-states', 'JVM platform thread states', total(metric('jvm_threads_states_threads'), GROUP + ',state'), 'short', 'Runnable, waiting, timed-waiting, blocked, new, terminated. JVM MXBean thread metrics do not enumerate all Java virtual threads.', legend='{{state}} · {{service.instance.id}}')
    d.panel('threads-live', 'Platform threads · live / daemon / peak', union(('live', metric('jvm_threads_live_threads')), ('daemon', metric('jvm_threads_daemon_threads')), ('peak', metric('jvm_threads_peak_threads'))), 'short', 'Live includes daemon and non-daemon threads; daemon is a subset, never add the two. Peak is since process start/reset.', legend='{{view}} · {{service.instance.id}}')
    d.panel('classes', 'Classes currently loaded', metric('jvm_classes_loaded_classes'), 'short', 'Current class count; compare against Metaspace trend. This is not cumulative classes ever loaded.', legend='{{service.instance.id}} · {{redis_mode}}')
    d.panel('class-churn', 'Class loading / unloading rate', union(('loaded', rate('jvm_classes_loaded_count_classes_total')), ('unloaded', rate('jvm_classes_unloaded_classes_total'))), 'ops', 'Counts/s, useful for class-loader churn. Interpret startup separately from steady state.', legend='{{view}} · {{service.instance.id}}')
    d.panel('fds', 'File descriptors · open / limit', union(('open', metric('process_files_open_files')), ('limit', metric('process_files_max_files'))), 'short', 'Unix descriptor accounting. Includes sockets and files; it is not the Redis connection count.', legend='{{view}} · {{service.instance.id}}')
    d.panel('jit', 'JIT compilation time per second', rate('jvm_compilation_time_ms_total') + ' / 1000', 'short', 'Compiler milliseconds/s converted to seconds/s; helps identify JVM warm-up activity. Not CPU core utilization.', legend='{{service.instance.id}}')
    for panel in d.doc['spec']['panels'].values():
        spec = panel['spec']['queries'][0]['spec']['plugin']['spec']
        spec['legend'] = spec['legend'].replace('{{service.instance.id}}', '{{service.name}}').replace(' · {{redis_mode}}', '')
        if spec['legend'] and '{{service.name}}' not in spec['legend']:
            spec['legend'] = '{{service.name}} · ' + spec['legend']
    d.write('dashboard.json')


def build_redis():
    variables = APP_VARIABLES + [variable('redis_instance', 'Redis exporter instance', 'service.instance.id')]
    d = Dashboard('Coregw MVC — Lettuce Pool & Redis Server',
                  'Client/pool metrics belong to the selected app; Redis server panels belong to the exporter and include ALL clients. '
                  'Compare equal traffic and separate time windows. Pool max-active is a limit, not a preallocated connection count.', variables)
    d.section('01 · Redis server overview — exporter scoped, all Redis clients')
    for key,title,expr,unit in [('redis-up','Redis reachable',f'min({metric("redis_up",scope=REDIS)})','short'),
                              ('clients-now','Connected clients',f'sum({metric("redis_connected_clients",scope=REDIS)})','short'),
                              ('redis-used','Redis allocated memory',f'sum({metric("redis_memory_used_bytes",scope=REDIS)})','bytes'),
                              ('redis-rss','Redis process RSS',f'sum({metric("redis_memory_used_rss_bytes",scope=REDIS)})','bytes')]:
        d.panel(key,title,expr,unit,'Current value. Includes all clients; app mode/environment filters do not scope Redis server metrics.',3,3,True)
    d.section('02 · Lettuce commands — client/network latency, outside pool acquisition')
    cmd='command=~"GET|SET|HSET"'
    d.panel('command-rate','GET / SET / HSET completion rate',total(rate('lettuce_command_completion_seconds.count',cmd),GROUP+',command'),'ops','Includes startup/prepare SETs; exclude those time windows for benchmark comparisons.',legend='{{command}} · {{service.instance.id}} · {{redis_mode}}')
    d.panel('command-mean','Command completion · mean',total(rate('lettuce_command_completion_seconds.sum',cmd),GROUP+',command')+' / '+total(rate('lettuce_command_completion_seconds.count',cmd),GROUP+',command'),'s','Lettuce client timing includes network and client scheduling; not Redis server execution alone.',legend='{{command}} · {{service.instance.id}} · {{redis_mode}}')
    for p in ['.90','.99']:
        d.panel('command-p'+p[1:],'Command completion · p'+p[1:],quantile('lettuce_command_completion_seconds',p,cmd,GROUP+',command'),'s','Histogram estimate per JVM, Redis mode and command. Does not include Commons Pool acquisition.',legend='{{command}} · {{service.instance.id}} · {{redis_mode}}')
    d.section('03 · Commons Pool — present only for mode=pool')
    d.panel('pool-connections','Pool active / idle / limit',union(*[(label,metric(name)) for label,name in [('active','redis_pool_active'),('idle','redis_pool_idle'),('limit','redis_pool_limit')]]),'short','Active + idle = current pooled native connections. Limit is max-active; connections are created on demand.',legend='{{view}} · {{service.instance.id}}')
    d.panel('pool-pressure','Pool utilization · active / limit',metric('redis_pool_active')+' / '+metric('redis_pool_limit'),'percentunit','Multiplexing has no Commons Pool series. A 5s scrape may miss short-lived saturation.',legend='{{service.instance.id}}',thresholds=[(.8,'#F59E0B','Review sustained saturation')])
    d.panel('pool-waiters','Callers waiting for a pooled connection',metric('redis_pool_waiters'),'short','Sampled count, not Redis blocked_clients. A zero sample cannot rule out a short wait between scrapes.',legend='{{service.instance.id}}')
    d.panel('pool-wait','Borrow wait · rolling mean / lifetime max',union(('rolling mean',metric('redis_pool_borrow_wait_mean_ms')),('lifetime max',metric('redis_pool_borrow_wait_max_ms'))),'ms','Commons Pool MXBean integer milliseconds; sub-ms waits can appear as 0. Max includes startup and is not a window p99.',legend='{{view}} · {{service.instance.id}}')
    d.panel('pool-borrows','Pool borrow rate',rate('redis_pool_borrowed'),'ops','Cumulative borrow count exposed as gauge; rate is interpreted as borrows/s and resets on process restart.',legend='{{service.instance.id}}')
    d.panel('pool-churn','Connection creations / destroys',union(('creations',rate('redis_pool_creations')),('destroys',rate('redis_pool_destroyed'))),'ops','Persistent churn after warm-up can explain higher CPU and latency. max-active does not preallocate connections.',legend='{{view}} · {{service.instance.id}}')
    d.section('04 · Redis CPU, connections, memory, traffic & failures')
    r=lambda name:metric(name,scope=REDIS)
    rr=lambda name:rate(name,scope=REDIS)
    d.panel('redis-cpu','Redis CPU · core equivalents',union(('user',rr('redis_cpu_user_seconds_total')),('system',rr('redis_cpu_sys_seconds_total'))),'short','CPU seconds/s; 1.0 is one core. Not directly comparable to JVM capacity fraction.',legend='{{view}} · {{service.instance.id}}')
    d.panel('redis-clients','Redis connected / blocked clients',union(('connected',r('redis_connected_clients')),('blocked',r('redis_blocked_clients'))),'short','Connected includes app, exporter and all other clients. Blocked means Redis blocking commands, not callers waiting for the Java pool.',legend='{{view}} · {{service.instance.id}}')
    d.panel('redis-memory','Redis allocated / RSS memory',union(('allocated',r('redis_memory_used_bytes')),('RSS',r('redis_memory_used_rss_bytes'))),'bytes','Redis allocator bytes vs physical resident process memory. RSS-minus-used is not proof of a leak.',legend='{{view}} · {{service.instance.id}}')
    d.panel('redis-memory-pressure','Redis memory / configured maxmemory',r('redis_memory_used_bytes')+' / ('+r('redis_memory_max_bytes')+' > 0)','percentunit','No series when maxmemory=0 (unlimited); do not display 0% as healthy for an unlimited server.',legend='{{service.instance.id}}',thresholds=[(.8,'#F59E0B','Example: inspect sustained pressure')])
    d.panel('redis-commands','Redis GET / SET / HSET throughput',rate('redis_commands_total','cmd=~"get|set|hset"',REDIS),'ops','Server-side command calls/s from ALL clients. Includes seeding and monitoring overhead.',legend='{{cmd}} · {{service.instance.id}}')
    d.panel('redis-execution','Redis command execution · mean',rate('redis_commands_duration_seconds_total','cmd=~"get|set|hset"',REDIS)+' / '+rate('redis_commands_total','cmd=~"get|set|hset"',REDIS),'s','Server execution mean from INFO commandstats, excluding client pool wait and network. Not a percentile.',legend='{{cmd}} · {{service.instance.id}}')
    d.panel('redis-network','Redis network input / output',union(('input',rr('redis_net_input_bytes_total')),('output',rr('redis_net_output_bytes_total'))),'Bps','All clients, including exporter.',legend='{{view}} · {{service.instance.id}}')
    d.panel('redis-hits','Redis keyspace hits / misses',union(('hits',rr('redis_keyspace_hits_total')),('misses',rr('redis_keyspace_misses_total'))),'ops','A cache miss is not necessarily an error. Benchmark preload keys are expected to hit.',legend='{{view}} · {{service.instance.id}}')
    d.panel('redis-rejections','Evictions / rejected connections',union(('evictions',rr('redis_evicted_keys_total')),('rejected connections',rr('redis_rejected_connections_total'))),'ops','Memory pressure and connection-capacity failures. Do not infer success from low Redis command latency alone.',legend='{{view}} · {{service.instance.id}}')
    d.panel('redis-scrape','App / exporter scrape availability',union(('app scrape',metric('up',scope=BASE)),('exporter scrape',metric('up',scope=REDIS)),('Redis reachable',r('redis_up'))),'short','Scrape and Redis up must be 1. Missing series is a telemetry gap, not zero usage.',legend='{{view}} · {{service.instance.id}}')
    d.write('redis-dashboard.json')


if __name__ == '__main__':
    build_jvm()
    build_redis()
    for name in ['dashboard.json', 'redis-dashboard.json']:
        d=json.loads((HERE/name).read_text())
        print(f'{name}: {len(d["spec"]["layouts"])} sections, {len(d["spec"]["panels"])} panels')
