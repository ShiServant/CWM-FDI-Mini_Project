'''
Memory Monitor (Linux)
This module collects memory performance (including total memory usage and memory growth) for the firefox process.
'''

import psutil
import time

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

def get_firefox_memory_usage():
    """
    Calculate total memory usage of all Firefox processes.

    Returns:
        float: Combined memory usage in bytes.
    """
    procs = find_firefox_processes()
    total_memory = 0.0

    for p in procs:
        try:
            mem_info = p.memory_info()
            total_memory += mem_info.rss  # Resident Set Size (physical memory)

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return total_memory

#Memory monitoring function: Interval 1s; No thresholds; Monitor memory growth over time
def monitor_firefox_memory(
    duration_seconds=60,
    check_interval_seconds=1
):
    """
    Monitor Firefox memory usage and detect memory bottlenecks.

    Args:
        duration_seconds (int): Total monitoring duration in seconds.
        check_interval_seconds (int): Interval between memory checks in seconds.
    """
    memory_usage_history = []
    start_time = time.time()

    while time.time() - start_time < duration_seconds:
        current_memory = get_firefox_memory_usage()
        memory_usage_history.append((time.time(), current_memory))

        # Display current reading
        print(f"Firefox Memory Usage: {current_memory / (1024 * 1024):.2f} MB")

        # Wait before next check
        time.sleep(check_interval_seconds)
#test code
if __name__ == "__main__":
    monitor_firefox_memory()
