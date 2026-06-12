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

import time
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

CPU_HIGH_PERCENT = 70.0      # average load per core above this is "high"
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
# Reads the current page's load time from the Performance API
# (Navigation Timing), plus performance.timeOrigin - a timestamp unique
# to each navigation, used to detect when a NEW page load has happened.
PAGE_LOAD_SCRIPT = (
    "const nav = performance.getEntriesByType('navigation')[0];"
    "if (!nav || nav.loadEventEnd === 0) { return null; }"
    "return [performance.timeOrigin, nav.loadEventEnd - nav.startTime];"
)

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
        # Remember the page that is already open so its (possibly old)
        # load time is not counted as a fresh navigation.
        self._last_time_origin = None
        result = self.client.execute_script(PAGE_LOAD_SCRIPT)
        if result is not None:
            self._last_time_origin = result[0]
        print("Connected to Firefox via Marionette; browse normally.\n")

    def responsiveness(self):
        """Round-trip time of a trivial execute_script() call (seconds)."""
        start = time.perf_counter()
        self.client.execute_script("return 0;")
        return time.perf_counter() - start

    def page_load_time(self):
        """
        Returns (load_seconds, is_new):
            load_seconds - the current page's load time, or None if a
                           page is still loading;
            is_new       - True only on the first reading after a new
                           page load completes, so each navigation is
                           counted once.
        """
        result = self.client.execute_script(PAGE_LOAD_SCRIPT)
        if result is None:
            return None, False

        time_origin, load_ms = result
        is_new = time_origin != self._last_time_origin
        self._last_time_origin = time_origin
        return load_ms / 1000.0, is_new

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
        Returns (load_seconds, is_new): a timed download of TEST_URL,
        measured every FETCH_EVERY_N_TICKS calls to limit network
        traffic. Every successful download is a fresh sample.
        """
        self._tick += 1
        if self._tick % FETCH_EVERY_N_TICKS != 0:
            return None, False

        try:
            start = time.perf_counter()
            with urllib.request.urlopen(
                TEST_URL, timeout=FETCH_TIMEOUT_SECONDS
            ) as response:
                response.read()
            return time.perf_counter() - start, True
        except OSError:
            return None, False

    def close(self):
        pass


# -------------------------------------------------
# Detection rules (evaluated on a full sliding window)
# -------------------------------------------------
def detect_cpu(avg_cpu, avg_resp, resp_threshold, resp_label):
    """
    CPU rules:
        Warning:    avg CPU > 70%  OR  avg responsiveness above threshold
        Bottleneck: avg CPU > 70%  AND avg responsiveness above threshold

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


def detect_network(cpu, current_mem_mb, total_mem_mb, page_load_seconds):
    """
    Network rule (possible network bottleneck), evaluated when a page
    load completes:
        CPU < 50%
        AND memory < 80% of system memory
        AND page load time > 5s
    """
    if page_load_seconds is None:
        return None

    cpu_low = cpu < CPU_LOW_PERCENT
    mem_ok = (current_mem_mb / total_mem_mb) < MEM_SYSTEM_FRACTION
    load_slow = page_load_seconds > PAGE_LOAD_SLOW_SECONDS

    if cpu_low and mem_ok and load_slow:
        return (
            "BOTTLENECK", "Network (possible)",
            f"page load {page_load_seconds:.2f}s > {PAGE_LOAD_SLOW_SECONDS:.0f}s while "
            f"CPU ({cpu:.1f}%) and memory are within normal range",
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
            page_load, load_is_new = metrics.page_load_time()

            cpu_window.append(cpu)
            mem_window.append(mem_mb)
            resp_window.append(resp)

            # ---- Status line ----
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if load_is_new:
                load_text = f"{page_load:.2f}s"
            else:
                load_text = "n/a"
            print(
                f"[{timestamp}] CPU: {cpu:5.1f}% | "
                f"Mem: {mem_mb:8.1f} MB ({mem_mb / total_mem_mb * 100:4.1f}% sys) | "
                f"{metrics.resp_label}: {resp:.3f}s | Load: {load_text}"
            )

            # ---- Network rule: fire as soon as a page load completes ----
            if load_is_new:
                network_alert = detect_network(cpu, mem_mb, total_mem_mb, page_load)
                if network_alert is not None:
                    severity, kind, details, recommendation = network_alert
                    print(
                        f"[{timestamp}] {severity:10s} | {kind} | {details}\n"
                        f"{'':21s} Recommendation: {recommendation}"
                    )

            # ---- CPU / memory rules (15-second sliding window) ----
            if len(cpu_window) == WINDOW_SECONDS:
                avg_cpu = sum(cpu_window) / len(cpu_window)
                avg_resp = sum(resp_window) / len(resp_window)
                mem_growth_mb = mem_window[-1] - mem_window[0]

                alerts = [
                    detect_cpu(
                        avg_cpu, avg_resp,
                        metrics.resp_threshold, metrics.resp_label,
                    ),
                    detect_memory(mem_mb, mem_growth_mb, total_mem_mb),
                ]

                for alert in alerts:
                    if alert is None:
                        continue
                    severity, kind, details, recommendation = alert
                    print(
                        f"[{timestamp}] {severity:10s} | {kind} | {details}\n"
                        f"{'':21s} Recommendation: {recommendation}"
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
