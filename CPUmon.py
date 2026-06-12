'''
CPU Monitor Module for Firefox Bottleneck Detector for linux
This module collects CPU usage data for the Firefox process and its child processes.
'''
import psutil
import time
from collections import deque


def find_firefox_processes():
    """
    Find all running Firefox processes.

    Firefox uses multiple processes (browser process,
    content processes, GPU process, etc.), so we collect
    all of them instead of monitoring only one PID.
    """
    procs = []

    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            # On Linux, Firefox child processes are named "Isolated Web Co",
            # "Web Content", "WebExtensions", etc. - the name alone misses
            # them, but their command line is the firefox binary plus
            # "-contentproc", so check both.
            name = (p.info["name"] or "").lower()
            cmdline = " ".join(p.info["cmdline"] or []).lower()

            if "firefox" in name or "firefox" in cmdline:
                procs.append(p)

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Ignore processes that disappear or cannot be accessed
            pass

    return procs


def get_firefox_cpu_percent():
    """
    Average CPU usage of all Firefox processes across all CPU cores.

    psutil reports each process as a percentage of one core (so values
    can add up to N*100 on an N-core machine). We sum every Firefox
    process, then divide by the number of logical CPUs so the result
    is the average load per core in the range 0-100%.

    Returns:
        float: Average Firefox CPU usage per core (%).
    """
    procs = find_firefox_processes()
    total_cpu = 0.0

    for p in procs:
        try:
            total_cpu += p.cpu_percent(interval=None)

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    cores = psutil.cpu_count(logical=True) or 1
    return total_cpu / cores


def monitor_firefox_cpu(
    duration_seconds=60,
    window_size=15
):
    """
    Monitor Firefox CPU usage and detect CPU bottlenecks.

    Parameters:
        duration_seconds:
            Total monitoring duration.

        window_size:
            Number of consecutive samples required
            before reporting a bottleneck.

    Example rule:
        CPU > 70% for 15 seconds
        -> CPU Bottleneck
    """

    # Store recent CPU measurements
    cpu_window = deque(maxlen=window_size)

    # -------------------------------------------------
    # Warm-up step
    # -------------------------------------------------
    # psutil needs an initial measurement before
    # cpu_percent() returns meaningful values.
    # -------------------------------------------------
    for p in find_firefox_processes():
        try:
            p.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    start_time = time.time()

    print("Starting Firefox CPU Monitoring...\n")

    while time.time() - start_time < duration_seconds:

        # Get current Firefox CPU usage
        cpu = get_firefox_cpu_percent()

        # Save sample into sliding window
        cpu_window.append(cpu)

        # Display current reading
        print(f"Firefox CPU Usage (avg per core): {cpu:.1f}%")

        # Wait 1 second before next measurement
        time.sleep(1)

#Example test code
if __name__ == "__main__":
    monitor_firefox_cpu()