'''
Network Monitor for Firefox Bottleneck Detector (Linux)
This module collects network performance (including upload/download speeds and latency) for the whole computer.
'''

import psutil
import time

def get_upload_speed(interval=1):
    """
    Measure upload speed over a given interval.

    Returns:
        upload_speed_bps (float): bytes per second
    """

    # Initial network counters
    start = psutil.net_io_counters()

    time.sleep(interval)

    # Network counters after interval
    end = psutil.net_io_counters()

    # Bytes uploaded during interval
    bytes_sent = end.bytes_sent - start.bytes_sent

    upload_speed_bps = bytes_sent / interval

    return upload_speed_bps

def get_download_speed(interval=1):
    """
    Measure download speed over a given interval.

    Returns:
        download_speed_bps (float): bytes per second
    """

    # Initial network counters
    start = psutil.net_io_counters()

    time.sleep(interval)

    # Network counters after interval
    end = psutil.net_io_counters()

    # Bytes downloaded during interval
    bytes_recv = end.bytes_recv - start.bytes_recv

    download_speed_bps = bytes_recv / interval

    return download_speed_bps

# Example test code
if __name__ == "__main__":
    while True:
        upload_speed = get_upload_speed()
        download_speed = get_download_speed()

        print(f"Upload Speed: {upload_speed * 8 / 1_000_000:.2f} Mbps")
        print(f"Download Speed: {download_speed * 8 / 1_000_000:.2f} Mbps")