'''
Firefox Bottleneck Detector (Linux)

Continuously monitors Firefox using a 15-second sliding window and reports
CPU, memory and network bottlenecks.

CPU and memory always come from CPUmon/Memmon. For in-browser metrics
(JS responsiveness, page load time) the detector picks the best
available mode, in this order:

1. Marionette (preferred):
   Firefox's built-in remote protocol, spoken over a plain TCP socket
   using only the standard library. Start Firefox yourself with:

       firefox -marionette &

2. Fallback (stdlib only, no browser connection):
   - Responsiveness proxy: scheduling lag of a short sleep.
   - Page load proxy: timed HTTP download of a small test URL.

Severity levels: WARNING and BOTTLENECK.
'''

import json
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime

import psutil

from CPUmon import find_firefox_processes, get_firefox_cpu_percent
from Memmon import get_firefox_memory_usage
from Marionette import MarionetteClient


# -------------------------------------------------
# Detection thresholds
# -------------------------------------------------
WINDOW_SECONDS = 15          # sliding window length (1 sample per second)

CPU_HIGH_PERCENT = 80.0      # average CPU above this is "high"
CPU_LOW_PERCENT = 50.0       # average CPU below this is "low" (network rule)
JS_SLOW_SECONDS = 0.5        # average JS responsiveness above this is "slow"
LAG_SLOW_SECONDS = 0.1       # fallback: average scheduling lag above this
MEM_SYSTEM_FRACTION = 0.80   # memory above 80% of total system memory
MEM_GROWTH_MB = 500.0        # memory growth within one window
PAGE_LOAD_SLOW_SECONDS = 5.0 # average page load above this is "slow"

# Fallback mode settings
TEST_URL = "https://www.example.com"  # small page used to time downloads
FETCH_EVERY_N_TICKS = 5               # fetch the test URL every 5 seconds
FETCH_TIMEOUT_SECONDS = 10


# -------------------------------------------------
# Metric providers
# -------------------------------------------------
# JavaScript snippet shared by the browser-connected modes: reads the
# current page's load time from the Performance API (Navigation Timing).
PAGE_LOAD_SCRIPT = (
    "const nav = performance.getEntriesByType('navigation')[0];"
    "if (!nav || nav.loadEventEnd === 0) { return null; }"
    "return nav.loadEventEnd - nav.startTime;"
)

# Chrome-context (privileged) script: per-process CPU time, memory and
# site origin via ChromeUtils.requestProcInfo() - the same API behind
# Firefox's about:processes page. With Fission, each website runs in its
# own process, so processes map cleanly to sites.
PROC_INFO_SCRIPT = """
const done = arguments[arguments.length - 1];
ChromeUtils.requestProcInfo().then(info => {
    const procs = [{
        pid: info.pid, type: 'parent', origin: '',
        memory: info.memory, cpuTime: String(info.cpuTime), pages: []
    }];
    for (const child of info.children) {
        let pages = [];
        try {
            pages = (child.windows || [])
                .filter(w => w.documentURI)
                .map(w => w.documentURI.spec)
                .slice(0, 5);
        } catch (e) {}
        procs.push({
            pid: child.pid, type: child.type, origin: child.origin || '',
            memory: child.memory, cpuTime: String(child.cpuTime),
            pages: pages
        });
    }
    done(JSON.stringify(procs));
}).catch(e => done(JSON.stringify({error: String(e)})));
"""

# Same data via a synchronous script that returns a Promise (Marionette
# awaits returned promises). Used automatically if the async-script
# variant fails on this Firefox version.
PROC_INFO_SCRIPT_SYNC = """
return ChromeUtils.requestProcInfo().then(info => {
    const procs = [{
        pid: info.pid, type: 'parent', origin: '',
        memory: info.memory, cpuTime: String(info.cpuTime), pages: []
    }];
    for (const child of info.children) {
        let pages = [];
        try {
            pages = (child.windows || [])
                .filter(w => w.documentURI)
                .map(w => w.documentURI.spec)
                .slice(0, 5);
        } catch (e) {}
        procs.push({
            pid: child.pid, type: child.type, origin: child.origin || '',
            memory: child.memory, cpuTime: String(child.cpuTime),
            pages: pages
        });
    }
    return JSON.stringify(procs);
}).catch(e => JSON.stringify({error: String(e)}));
"""

# Friendly names for Firefox's non-website helper processes
PROCESS_TYPE_NAMES = {
    "parent": "Firefox main process (UI)",
    "gpu": "GPU process",
    "socket": "network I/O process",
    "extension": "extensions process",
    "rdd": "media decoder process",
    "utility": "utility process",
    "preallocated": "preallocated (idle) process",
    "privilegedabout": "about: pages",
    "web": "shared web content",
}


def _site_label(proc):
    """Human-readable owner of a process: site origin, page host or type."""
    origin = proc.get("origin", "")
    if origin and origin not in ("null",):
        return origin.split("^")[0]  # strip partition suffixes

    for url in proc.get("pages") or []:
        host = urllib.parse.urlsplit(url).netloc
        if host:
            return host

    proc_type = proc.get("type", "unknown")
    return PROCESS_TYPE_NAMES.get(proc_type, f"{proc_type} process")


class MarionetteMetrics:
    """
    Responsiveness and page load measured inside the browser via
    Firefox's built-in Marionette protocol (standard library only).

    Requires Firefox to be started with:  firefox -marionette
    """

    mode_name = "Marionette (built into Firefox, no installs needed)"
    resp_label = "JS"
    resp_threshold = JS_SLOW_SECONDS

    def __init__(self):
        self.client = MarionetteClient()
        self._prev_cpu_ns = {}      # pid -> cumulative CPU time (ns)
        self._prev_sample_time = None
        self.latest_consumers = []  # refreshed every tick by sample_consumers()
        self._consumer_error_shown = False
        self._use_sync_procinfo = False
        print("Connected to Firefox via Marionette; browse normally.\n")

    def _consumer_failure(self, reason):
        """Warn once (not every tick) when per-site attribution fails."""
        if not self._consumer_error_shown:
            self._consumer_error_shown = True
            print(
                f"Note: per-site attribution unavailable ({reason}); "
                "alerts will fall back to process-level data.\n"
            )

    def sample_consumers(self):
        """
        Refresh self.latest_consumers: per-site CPU% and memory, aggregated
        over Firefox's processes. CPU% is computed from the growth of each
        process's cumulative CPU time since the previous call (~1s ago).
        """
        try:
            self.client.set_context("chrome")
            try:
                if self._use_sync_procinfo:
                    raw = self.client.execute_script(PROC_INFO_SCRIPT_SYNC)
                else:
                    try:
                        raw = self.client.execute_async_script(PROC_INFO_SCRIPT)
                    except RuntimeError:
                        # Async chrome scripts unsupported here; switch to
                        # the synchronous promise-based variant for good.
                        self._use_sync_procinfo = True
                        raw = self.client.execute_script(PROC_INFO_SCRIPT_SYNC)
            finally:
                self.client.set_context("content")
            procs = json.loads(raw)
        except (RuntimeError, ValueError, TypeError) as exc:
            self._consumer_failure(str(exc))
            return  # privileged API unavailable; keep last known data

        if not isinstance(procs, list):  # {'error': ...} from the script
            self._consumer_failure(str(procs.get("error", procs)))
            return

        now = time.perf_counter()
        wall_ns = (
            (now - self._prev_sample_time) * 1e9
            if self._prev_sample_time is not None else None
        )

        groups = {}
        new_cpu_ns = {}
        for proc in procs:
            pid = proc["pid"]
            cpu_ns = float(proc["cpuTime"])
            new_cpu_ns[pid] = cpu_ns

            label = _site_label(proc)
            group = groups.setdefault(
                label, {"cpu": 0.0, "mem_mb": 0.0, "procs": 0, "has_cpu": False}
            )
            group["mem_mb"] += proc["memory"] / (1024 * 1024)
            group["procs"] += 1
            if wall_ns and pid in self._prev_cpu_ns:
                delta = max(0.0, cpu_ns - self._prev_cpu_ns[pid])
                group["cpu"] += delta / wall_ns * 100.0
                group["has_cpu"] = True

        self._prev_cpu_ns = new_cpu_ns
        self._prev_sample_time = now

        consumers = [
            {
                "label": label,
                "cpu_percent": g["cpu"] if g["has_cpu"] else None,
                "memory_mb": g["mem_mb"],
                "processes": g["procs"],
            }
            for label, g in groups.items()
        ]
        consumers.sort(
            key=lambda c: (c["cpu_percent"] or 0.0, c["memory_mb"]),
            reverse=True,
        )
        self.latest_consumers = consumers

    def responsiveness(self):
        """Round-trip time of a trivial execute_script() call (seconds)."""
        start = time.perf_counter()
        self.client.execute_script("return 0;")
        return time.perf_counter() - start

    def page_load_time(self):
        """Current page's load time in seconds, or None if still loading."""
        load_ms = self.client.execute_script(PAGE_LOAD_SCRIPT)
        return None if load_ms is None else load_ms / 1000.0

    def close(self):
        self.client.close()


class FallbackMetrics:
    """
    Standard-library-only approximations, used when the Marionette
    connection is unavailable. Monitors the Firefox instance you
    started yourself.

    - Responsiveness proxy: how much longer a 50 ms sleep takes than
      requested (scheduling lag). Under heavy CPU contention this lag
      grows, mirroring how the browser UI becomes sluggish.
    - Page load proxy: time to download a small test page with urllib.
      This reflects network health rather than full page rendering.
    """

    mode_name = "fallback (no browser connection, stdlib only)"
    resp_label = "Lag"
    resp_threshold = LAG_SLOW_SECONDS

    def __init__(self):
        self._tick = 0
        print("No browser connection available - running in fallback mode.")
        print("Start Firefox yourself and browse normally.\n")

    def responsiveness(self):
        """Scheduling lag of a 50 ms sleep (seconds)."""
        requested = 0.05
        start = time.perf_counter()
        time.sleep(requested)
        return max(0.0, time.perf_counter() - start - requested)

    def page_load_time(self):
        """
        Timed download of TEST_URL (seconds), measured every
        FETCH_EVERY_N_TICKS calls to limit network traffic.
        Returns None on the other ticks or if the fetch fails.
        """
        self._tick += 1
        if self._tick % FETCH_EVERY_N_TICKS != 0:
            return None

        try:
            start = time.perf_counter()
            with urllib.request.urlopen(
                TEST_URL, timeout=FETCH_TIMEOUT_SECONDS
            ) as response:
                response.read()
            return time.perf_counter() - start
        except OSError:
            return None

    def close(self):
        pass


def top_firefox_processes(limit=3, sample_seconds=0.25):
    """
    psutil-based attribution used when Marionette data is unavailable:
    ranks individual Firefox processes by CPU over a short sample.
    On Linux the process names hint at the role ("Isolated Web Co" =
    website content, "WebExtensions", "GPU Process", ...), but cannot
    name the specific website.
    """
    procs = find_firefox_processes()
    for p in procs:
        try:
            p.cpu_percent(interval=None)  # prime the measurement
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    time.sleep(sample_seconds)

    entries = []
    for p in procs:
        try:
            entries.append({
                "label": f"{p.name()} (pid {p.pid})",
                "cpu_percent": p.cpu_percent(interval=None),
                "memory_mb": p.memory_info().rss / (1024 * 1024),
                "processes": 1,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    entries.sort(
        key=lambda c: (c["cpu_percent"] or 0.0, c["memory_mb"]), reverse=True
    )
    return entries[:limit]


# -------------------------------------------------
# Detection rules (evaluated on a full sliding window)
# -------------------------------------------------
def detect_cpu(avg_cpu, avg_resp, resp_threshold, resp_label):
    """
    CPU rules:
        Warning:    avg CPU > 80%  OR  avg responsiveness above threshold
        Bottleneck: avg CPU > 80%  AND avg responsiveness above threshold

    The responsiveness threshold is 0.5s for in-browser JS latency
    (Marionette mode), 0.1s for the fallback scheduling-lag proxy.
    """
    cpu_high = avg_cpu > CPU_HIGH_PERCENT
    resp_slow = avg_resp > resp_threshold

    if cpu_high and resp_slow:
        return (
            "BOTTLENECK", "CPU",
            f"avg CPU {avg_cpu:.1f}% > {CPU_HIGH_PERCENT:.0f}% and "
            f"avg {resp_label} responsiveness {avg_resp:.3f}s > {resp_threshold}s",
            "Close heavy tabs, disable unused extensions and enable "
            "hardware acceleration.",
        )

    if cpu_high or resp_slow:
        return (
            "WARNING", "CPU",
            f"avg CPU {avg_cpu:.1f}% (limit {CPU_HIGH_PERCENT:.0f}%), "
            f"avg {resp_label} responsiveness {avg_resp:.3f}s (limit {resp_threshold}s)",
            "Monitor CPU-heavy tabs; consider closing background tabs.",
        )

    return None


def detect_memory(current_mem_mb, mem_growth_mb, total_mem_mb):
    """
    Memory rules:
        Warning:    memory > 80% of system memory  OR  growth > 500MB / window
        Bottleneck: memory > 80% of system memory  AND growth > 500MB / window
    """
    mem_fraction = current_mem_mb / total_mem_mb
    mem_high = mem_fraction > MEM_SYSTEM_FRACTION
    growth_high = mem_growth_mb > MEM_GROWTH_MB

    if mem_high and growth_high:
        return (
            "BOTTLENECK", "Memory",
            f"memory {current_mem_mb:.0f} MB ({mem_fraction * 100:.1f}% of system) and "
            f"growth {mem_growth_mb:.0f} MB in {WINDOW_SECONDS}s",
            "Restart Firefox or close memory-heavy tabs; check for leaking "
            "pages or extensions.",
        )

    if mem_high or growth_high:
        return (
            "WARNING", "Memory",
            f"memory {current_mem_mb:.0f} MB ({mem_fraction * 100:.1f}% of system), "
            f"growth {mem_growth_mb:.0f} MB in {WINDOW_SECONDS}s "
            f"(limits: {MEM_SYSTEM_FRACTION * 100:.0f}%, {MEM_GROWTH_MB:.0f} MB)",
            "Watch memory usage; close tabs you no longer need.",
        )

    return None


def detect_network(avg_cpu, current_mem_mb, total_mem_mb, avg_load):
    """
    Network rule (possible network bottleneck):
        avg CPU < 50%
        AND memory < 80% of system memory
        AND avg page load time > 5s
    """
    if avg_load is None:
        return None

    cpu_low = avg_cpu < CPU_LOW_PERCENT
    mem_ok = (current_mem_mb / total_mem_mb) < MEM_SYSTEM_FRACTION
    load_slow = avg_load > PAGE_LOAD_SLOW_SECONDS

    if cpu_low and mem_ok and load_slow:
        return (
            "BOTTLENECK", "Network (possible)",
            f"avg page load {avg_load:.2f}s > {PAGE_LOAD_SLOW_SECONDS:.0f}s while "
            f"CPU ({avg_cpu:.1f}%) and memory are within normal range",
            "Check connection speed, Wi-Fi signal and DNS; the slowdown is "
            "likely network- or server-side, not Firefox.",
        )

    return None


# -------------------------------------------------
# Main monitoring loop
# -------------------------------------------------
def create_metrics():
    """
    Pick the best available metric provider:
    Marionette -> stdlib fallback.
    """
    try:
        return MarionetteMetrics()
    except OSError:
        print(
            "Marionette not reachable on port 2828.\n"
            "(To enable it: close Firefox, then run  firefox -marionette)\n"
        )

    return FallbackMetrics()


def run_detector(duration_seconds=None):
    """
    Continuously monitor Firefox and report bottlenecks.

    Args:
        duration_seconds: total run time, or None to run until
            interrupted (Ctrl+C) or the browser window is closed.
    """
    metrics = create_metrics()
    print(f"Monitoring mode: {metrics.mode_name}\n")

    total_mem_mb = psutil.virtual_memory().total / (1024 * 1024)

    cpu_window = deque(maxlen=WINDOW_SECONDS)
    mem_window = deque(maxlen=WINDOW_SECONDS)
    resp_window = deque(maxlen=WINDOW_SECONDS)
    load_window = deque(maxlen=WINDOW_SECONDS)

    # Warm-up: psutil needs an initial call before
    # cpu_percent() returns meaningful values.
    for p in find_firefox_processes():
        try:
            p.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    start_time = time.time()

    try:
        while duration_seconds is None or time.time() - start_time < duration_seconds:
            tick_start = time.perf_counter()

            # ---- Collect one sample of each metric ----
            cpu = get_firefox_cpu_percent()
            mem_mb = get_firefox_memory_usage() / (1024 * 1024)
            resp = metrics.responsiveness()
            page_load = metrics.page_load_time()

            cpu_window.append(cpu)
            mem_window.append(mem_mb)
            resp_window.append(resp)
            if page_load is not None:
                load_window.append(page_load)

            # Keep per-site attribution fresh (Marionette mode only),
            # so CPU% deltas cover the last ~1 second.
            if hasattr(metrics, "sample_consumers"):
                metrics.sample_consumers()

            # ---- Status line ----
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            load_text = f"{page_load:.2f}s" if page_load is not None else "n/a"
            print(
                f"[{timestamp}] CPU: {cpu:5.1f}% | "
                f"Mem: {mem_mb:8.1f} MB ({mem_mb / total_mem_mb * 100:4.1f}% sys) | "
                f"{metrics.resp_label}: {resp:.3f}s | Load: {load_text}"
            )

            # ---- Evaluate rules once the window is full ----
            if len(cpu_window) == WINDOW_SECONDS:
                avg_cpu = sum(cpu_window) / len(cpu_window)
                avg_resp = sum(resp_window) / len(resp_window)
                mem_growth_mb = mem_window[-1] - mem_window[0]
                avg_load = (
                    sum(load_window) / len(load_window) if load_window else None
                )

                alerts = [
                    detect_cpu(
                        avg_cpu, avg_resp,
                        metrics.resp_threshold, metrics.resp_label,
                    ),
                    detect_memory(mem_mb, mem_growth_mb, total_mem_mb),
                    detect_network(avg_cpu, mem_mb, total_mem_mb, avg_load),
                ]

                fired = [alert for alert in alerts if alert is not None]

                for severity, kind, details, recommendation in fired:
                    print(
                        f"[{timestamp}] {severity:10s} | {kind} | {details}\n"
                        f"{'':21s} Recommendation: {recommendation}"
                    )

                # ---- Attribute the load to specific sites/processes ----
                if fired:
                    consumers = getattr(metrics, "latest_consumers", None)
                    if not consumers:
                        consumers = top_firefox_processes()
                    if consumers:
                        print(f"{'':21s} Top resource consumers:")
                        for c in consumers[:3]:
                            cpu_text = (
                                f"{c['cpu_percent']:5.1f}%"
                                if c["cpu_percent"] is not None else "  n/a"
                            )
                            print(
                                f"{'':21s}   - {c['label']}: "
                                f"CPU {cpu_text}, "
                                f"Mem {c['memory_mb']:7.1f} MB "
                                f"({c['processes']} process(es))"
                            )

            # ---- Keep a 1 sample/second cadence ----
            elapsed = time.perf_counter() - tick_start
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)

    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")
    except ConnectionError:
        print("\nBrowser connection lost (window closed?); monitoring stopped.")
    finally:
        metrics.close()


if __name__ == "__main__":
    run_detector()
