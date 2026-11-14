# Coregw operations dashboards

Reference: [SigNoz JVM Metrics Dashboard](https://signoz.io/docs/dashboards/dashboard-templates/jvm-metrics/)
and its v6 template. This implementation uses the existing Actuator/Micrometer metrics,
not the agent's `jvm.*` names. No duplicate JVM telemetry source is enabled.

## Views

1. **Coregw — MVC / WebFlux · JVM & HTTP Operations** (`dashboard.json`, replaces the original dashboard at the same URL):
   overview, HTTP workload, CPU/lifecycle, JVM memory, garbage collection, threads/classes/process resources.
2. **Coregw MVC — Lettuce Pool & Redis Server** (`redis-dashboard.json`):
   Redis health/clients/memory overview, Lettuce commands, Commons Pool, Redis CPU/memory/traffic/errors.

The split keeps application diagnosis separate from Redis server/client diagnosis. CPU, heap, GC and
HTTP series are grouped by **JVM instance + redis_mode**. Per-instance percentages are not summed,
and p99 values are not averaged across JVMs. Server Redis metrics include ALL clients, independent
of app mode; compare separate time windows with equal workload. Thresholds shown at CPU/heap 80%,
GC pause 5%, HTTP 5xx 1% are starting points for investigation, **not agreed SLOs or active alert rules**.

## Filters and units

- **Service**: shared selector for `coregw-mvc` and `coregw-webflux`; use ALL and select each benchmark time range.
- **JVM instance**: select one for diagnosis, or All for per-instance comparison.
- **Redis mode**: pool / multiplexing; All preserves both historical series.
- **Environment**: `DEPLOYMENT_ENVIRONMENT`, default `local`, after app restart. All includes legacy unlabeled series.
- **Rate window**: fixed 1m for consistent operational comparisons. To change it, adjust the generator and re-provision both dashboards together; UI time range is still selectable.
- Redis view also has **Redis exporter instance**. Only exporter targets (currently `host.docker.internal:9121`)
  match Redis panels; choosing the JVM target yields no data.

Prometheus `job` / `instance` are translated into resource `service.name` / `service.instance.id`.
The collector queries still target 8080 for app and 9121 for Redis exporter. It is normal to see the
exporter's address on a Redis server series. It is not the Redis TCP listener address.

CPU capacity fraction differs from core equivalents (`CPU seconds / wall seconds`). Heap/non-heap
are JVM allocations, not process RSS. Java thread MXBeans count platform threads, not all Java virtual
threads. Live threads include daemon threads; do not add live + daemon. Max heap excludes undefined
per-pool values (-1). maxmemory=0 means Redis unlimited, not 0% pressure.

## Mapping from the reference JVM template

| Reference OTel metric / area | Current Micrometer metric / panel |
|---|---|
| `jvm.cpu.count` | `system_cpu_count` / processors visible to JVM |
| `jvm.cpu.time` | `process_cpu_time_ns_total` / rate converted from ns to core equivalents |
| `jvm.cpu.recent_utilization` | `process_cpu_usage` / fraction of available CPU capacity |
| `jvm.memory.used` | `jvm_memory_used_bytes`, split heap/nonheap and by `id` |
| `jvm.memory.committed` / limit | `jvm_memory_committed_bytes` / positive `jvm_memory_max_bytes` |
| G1 Old / Eden / Survivor | actual heap pool `id` labels; no assumed pool names for other GCs |
| `jvm.gc.duration` | `jvm_gc_pause_seconds` / pause cost, frequency, mean, histogram p90/p99 |
| thread states / daemon / live | `jvm_threads_states_threads`, `jvm_threads_daemon_threads`, `jvm_threads_live_threads` |
| class loading / unloading | `jvm_classes_loaded_classes`, loaded/unloaded counters |
| additional process diagnostics | direct/mapped buffers, file descriptors, uptime, JIT activity |

## Histogram prerequisites

`application.yml` enables histograms for HTTP and `jvm.gc.pause`, and the environment tag.
**Restart the app** to apply these settings. GC p90/p99 cannot be reconstructed from existing count/sum
history. Until restart, GC mean still works; histogram curves have no data. Even after enabling buckets,
no GC in a selected window means no GC duration percentile. Likewise HTTP/Lettuce p99 needs actual
commands/requests in the window. No-data is kept distinct from zero or healthy.

After the Prometheus receiver, the SigNoz names are `.bucket`, `.count`, `.sum`, e.g.
`{__name__="jvm_gc_pause_seconds.bucket"}`. Counter `_total` names remain unchanged. The generator
uses selectors with exact names. Histograms are approximations; k6 report remains the end-to-end view.

Pool sections are absent in multiplexing mode by design. Pool waits from MXBeans are integer ms,
rolling sample mean and lifetime max, not p99. Gauges scraped every 5s may miss short contention.
The Redis `blocked_clients` gauge reports Redis blocking commands, not Java callers waiting for a pool.

## Rebuild, provision, validate

```sh
python3 coregw-mvc/monitoring/signoz/build_dashboards.py
python3 coregw-mvc/monitoring/signoz/provision_dashboards.py
# Validate queries without writing dashboards:
python3 coregw-mvc/monitoring/signoz/provision_dashboards.py --validate-only
```

Provisioning reuses IDs stored locally in `.cache/signoz/dashboard-instances.json`, keeps the existing
main dashboard ID, and backs up replaced JSON to `.cache/signoz/backups/<UTC timestamp>/`.
It uses credentials in `.cache/signoz/admin-login.json`, or `SIGNOZ_EMAIL`, `SIGNOZ_PASSWORD`,
`SIGNOZ_ORG_ID` environment variables. Credentials/tokens are not stored in tracked config or printed.
It records query checks in `.cache/signoz/dashboard-validation.json`; missing data is reported separately
from query errors. On a new installation use Import JSON in SigNoz or provision after creating the account.

This is an operations-oriented dashboard design, not a claim that the local Docker installation is a
production deployment. Actual production requires target discovery, retention/capacity planning and
SLO/alert thresholds appropriate to the workload. A scrape gap should be investigated before comparing
benchmarks; a missing chart does not prove zero load. Sleeping/restarting the Docker host can also leave
series metadata stale; check `up` and current samples rather than assuming the dashboard query is wrong.


## Local ClickHouse resource tuning

While checking the dashboards we observed ClickHouse query memory errors on the 7.65 GiB Docker VM.
`casting.yaml` now persists local profile settings: 2 query threads, 1 insert thread, 1 GiB per-query
limit, aggregation/sort spill at 64 MiB. Existing volumes/history are preserved. These lower peak
parallel memory use and can increase query latency/disk I/O; they are local sizing decisions, not
production capacity recommendations. Query and ingestion errors must still be monitored under the
actual retention/cardinality/load. The updated profile was reloaded live and verified via system.settings.

Latest verification: all 57 panel queries succeeded against SigNoz. Two transient backend-memory
errors required retries during the final pass; this is recorded per panel in the local validation
report. The dashboard design is verified, but these local resource settings do not establish
production capacity or guarantee that overload cannot recur. GC histogram, HTTP and Lettuce
percentiles were verified using a separate 8081 process, which was then stopped; collector target
is restored to the user's app at 8080.

### Tomcat worker capacity

MVC enables `server.tomcat.mbeanregistry.enabled=true`; restart the application after updating config.
The JVM dashboard includes Tomcat busy/current/max threads, busy/max utilization and current/keepalive/max connections, grouped by JVM and connector.
Check `tomcat_threads_busy_threads` and `tomcat_threads_config_max_threads` at `/actuator/prometheus` after restart; collector scrapes every 5 seconds.
Sustained busy/max near 100% indicates platform-worker saturation, but does not measure queue length or queue wait. Open connections minus busy threads is not a queue metric. Virtual-thread mode requires separate interpretation; these gauges must not be treated as the number/limit of virtual requests.
No series before restart means unavailable telemetry, not zero busy threads. Prior benchmark queueing cannot be reconstructed from these newly enabled gauges.

### Native memory and scheduling on macOS

`./coregw-mvc/monitoring/signoz/start-native-metrics.sh` starts the host Python exporter on `127.0.0.1:9257/metrics`; `setup.sh` also starts it on macOS. No Python packages or root access are needed. It discovers the MVC or WebFlux JVM listening on 8080 every 5s using its main class/module and follows PID changes. It ignores unrelated applications. Logs/PID: `.cache/signoz/native-metrics.log` and `native-metrics.pid`. This host process survives terminal closure but must be started again after reboot. Stop using `kill "$(cat .cache/signoz/native-metrics.pid)"` after verifying the PID belongs to this exporter.

The collector scrapes it under the detected MVC/WebFlux resource identity, with `telemetry_source=native_os` and `process_pid` to distinguish processes. The new dashboard section contains RSS, process context switches/s, NMT thread stack and Thread category reserved/committed bytes, availability and NMT sample age.

- RSS and context switches come from Darwin `proc_pidinfo(PROC_PIDTASKINFO)`; they cover the whole JVM process. Context switches do not measure switching time or identify only Tomcat threads. Darwin's 32-bit context switch counter can wrap; the rate expression handles drops as resets (the wrap interval can have a small error).
- Add `-XX:NativeMemoryTracking=summary` to IntelliJ **VM options**, then restart the application. NMT cannot be enabled by changing application.yml. The exporter uses the target JVM's sibling `jcmd` every 30s, asynchronously from scrapes; attach must be permitted. `coregw_nmt_available=0` means missing/disabled NMT, not zero stack memory. `jcmd` has a 5s timeout. Compare benchmark modes with the same NMT setting because tracking and polling add overhead.
- NMT stack reserved is virtual address space, committed is JVM memory accounting, neither is RSS. The Thread category includes stacks and bookkeeping; do not add the two panels. These are native thread stacks, not heap-resident virtual-thread stacks. NMT does not account for all allocations by third-party native libraries.
- The exporter only provides current/future data. There is no backfill for previous benchmark runs. The exporter automatically follows MVC/WebFlux on port 8080; `.cache/signoz/discovery/app-targets.json` atomically supplies the collector target identity. Keep the host exporter running for both app and native metric discovery.

### Shared MVC / WebFlux dashboard

Use the same JVM/HTTP dashboard and choose the benchmark time range; only one application runs on 8080 at a time. Service ALL includes both applications. CPU, heap, GC, RSS, context switches and NMT panels are shared. Tomcat panels are MVC-only; Reactor Netty connection pool panels are WebFlux-only. Missing platform-specific series are expected, not zero. App identity is discovered by the host exporter and passed through Prometheus file service discovery. New WebFlux HTTP/GC histogram settings require restarting WebFlux from the rebuilt resources; old data is not backfilled or relabeled.
