'''
Testing and Collection of Data (Linux)
'''

# Use the functions from the other monitoring modules to collect data.

import time

from CPUmon import find_firefox_processes, get_firefox_cpu_percent
from Memmon import get_firefox_memory_usage
from Netmon import get_upload_speed, get_download_speed


def collect_data(duration_seconds=300):
    """
    Collect CPU, memory and network samples once per second.

    Returns:
        list of dicts, one per sample:
        {timestamp, cpu_percent, memory_mb, upload_mbps, download_mbps}
    """
    samples = []

    # Warm-up: psutil needs an initial call before
    # cpu_percent() returns meaningful values.
    for p in find_firefox_processes():
        try:
            p.cpu_percent(interval=None)
        except Exception:
            pass

    start_time = time.time()
    print("Starting data collection...\n")

    while time.time() - start_time < duration_seconds:
        # Each network measurement sleeps for its interval, so the two
        # calls together (0.5s + 0.5s) also pace the loop at ~1 sample/sec.
        upload_bps = get_upload_speed(interval=0.5)
        download_bps = get_download_speed(interval=0.5)

        # CPU usage since the previous call (~1 second ago)
        cpu_percent = get_firefox_cpu_percent()

        # Current memory usage in bytes
        memory_bytes = get_firefox_memory_usage()

        sample = {
            "timestamp": time.time(),
            "cpu_percent": cpu_percent,
            "memory_mb": memory_bytes / (1024 * 1024),
            "upload_mbps": upload_bps * 8 / 1_000_000,
            "download_mbps": download_bps * 8 / 1_000_000,
        }
        samples.append(sample)

        elapsed = sample["timestamp"] - start_time
        print(
            f"[{elapsed:5.1f}s] "
            f"CPU: {sample['cpu_percent']:5.1f}% | "
            f"Memory: {sample['memory_mb']:8.2f} MB | "
            f"Upload: {sample['upload_mbps']:7.2f} Mbps | "
            f"Download: {sample['download_mbps']:7.2f} Mbps"
        )

    print(f"\nCollected {len(samples)} samples over {duration_seconds} seconds.")
    return samples


if __name__ == "__main__":
    collect_data(duration_seconds=60)
