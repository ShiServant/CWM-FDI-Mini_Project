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

    for p in psutil.process_iter(["name"]):
        try:
            # Convert process name to lowercase for comparison
            name = (p.info["name"] or "").lower()

            if "firefox" in name:
                procs.append(p)

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Ignore processes that disappear or cannot be accessed
            pass

    return procs


def get_firefox_cpu_percent():
    """
    Calculate total CPU usage of all Firefox processes.

    Returns:
        float: Combined CPU usage percentage.
    """
    procs = find_firefox_processes()
    total_cpu = 0.0

    for p in procs:
        try:
            total_cpu += p.cpu_percent(interval=None)

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return total_cpu


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
        CPU > 80% for 15 seconds
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
        print(f"Firefox CPU Usage: {cpu:.1f}%")

        # Wait 1 second before next measurement
        time.sleep(1)

#Example test code
if __name__ == "__main__":
    monitor_firefox_cpu()