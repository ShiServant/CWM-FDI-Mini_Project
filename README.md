# Firefox Bottleneck Detector (Linux)

A mini project for CWM-FDI that monitors a running Firefox browser and
detects CPU, memory and network bottlenecks in real time, using a
15-second sliding window of per-second samples.

## Project structure

| File | Purpose |
| --- | --- |
| `CPUmon.py` | Finds all Firefox processes and measures their combined CPU usage (%). |
| `Memmon.py` | Measures combined Firefox memory usage (RSS, bytes). |
| `Marionette.py` | Minimal client for Firefox's built-in Marionette remote protocol (standard library only). |
| `Detector.py` | Main program: collects all metrics, applies the bottleneck rules and prints alerts. |

## Metrics

- **Firefox CPU usage (%)** — sum over all Firefox processes (`psutil`).
- **Firefox memory usage (MB)** — sum of RSS over all Firefox processes.
- **JavaScript responsiveness (s)** — round-trip time of a trivial
  `execute_script()` call inside the browser.
- **Page load time (s)** — from the browser Performance API
  (Navigation Timing: `loadEventEnd - startTime`).

## Detection rules

All averages are over a 15-second sliding window (1 sample/second).
Two severity levels are reported: `WARNING` and `BOTTLENECK`.

**CPU**

- Warning: average CPU > 80% **or** average JS responsiveness > 0.5 s
- Bottleneck: average CPU > 80% **and** average JS responsiveness > 0.5 s

**Memory**

- Warning: memory > 80% of total system memory **or** growth > 500 MB
  within the window
- Bottleneck: memory > 80% of total system memory **and** growth > 500 MB
  within the window

**Network (possible bottleneck)**

- Average CPU < 50% **and** memory < 80% of system memory **and**
  average page load time > 5 s

Thresholds are constants at the top of `Detector.py` and can be tuned.

When an alert fires, the detector also prints the **top resource
consumers**. In Marionette mode this is per website (via
`ChromeUtils.requestProcInfo()`, the API behind `about:processes` -
with Fission every site runs in its own process, so CPU and memory can
be attributed to specific origins). In fallback mode it falls back to
ranking individual Firefox processes by CPU/memory, which shows the
process role (e.g. "Isolated Web Co") but not the website name.

## Requirements

- Linux, Python 3.8+
- `psutil` (e.g. `sudo apt install python3-psutil`)

Everything else is the Python standard library. On externally-managed
systems (PEP 668), install psutil through `apt` as shown above, or use
a virtual environment:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python Detector.py
```

## Running the detector

`Detector.py` picks the best available way to measure the in-browser
metrics, in this order:

### 1. Marionette mode (preferred — no installs needed)

Marionette is Firefox's built-in remote-control protocol (the same one
geckodriver uses). `Marionette.py` speaks it over a plain TCP socket.

```bash
# Close Firefox completely first - the flag only works on a fresh launch
pkill firefox

# Start Firefox with Marionette enabled (robot icon appears in the URL bar)
firefox -marionette &

# Verify it is listening (should show 127.0.0.1:2828)
ss -tln | grep 2828

# Run the detector
python3 Detector.py
```

You browse in your normal Firefox window; the detector connects to it
from outside. You can test the connection on its own with
`python3 Marionette.py`.

### 2. Fallback mode (stdlib only)

Used automatically if the Marionette connection is unavailable.
Approximates the two in-browser metrics:

- responsiveness → scheduling lag of a 50 ms sleep (threshold 0.1 s
  instead of 0.5 s),
- page load → timed HTTP download of a small test page every 5 s.

The detector prints which mode it chose at startup. Stop it with
`Ctrl+C`.

### Example output

```
Monitoring mode: Marionette (built into Firefox, no installs needed)

[2026-06-12 10:03:20] CPU:  87.3% | Mem:   2911.4 MB (18.2% sys) | JS: 0.612s | Load: 4.10s
[2026-06-12 10:03:21] BOTTLENECK | CPU | avg CPU 85.1% > 80% and avg JS responsiveness 0.587s > 0.5s
                      Recommendation: Close heavy tabs, disable unused extensions and enable hardware acceleration.
                      Top resource consumers:
                        - https://www.youtube.com: CPU  72.4%, Mem   812.3 MB (2 process(es))
                        - Firefox main process (UI): CPU  21.0%, Mem   654.1 MB (1 process(es))
                        - GPU process: CPU   8.2%, Mem   178.9 MB (1 process(es))
```

## Other scripts

```bash
python3 CPUmon.py       # CPU monitor only
python3 Memmon.py       # memory monitor only
python3 Marionette.py   # test the Marionette connection
```

## Troubleshooting

- **`ConnectionRefusedError` (errno 111) from `Marionette.py`** —
  nothing is listening on port 2828. Firefox must be *fully closed*
  before launching with `-marionette`; otherwise the flag is silently
  ignored. Check with `pgrep -af firefox` that the process command line
  contains `-marionette`, and that `ss -tln | grep 2828` shows a
  listener. For snap/flatpak installs try `MOZ_MARIONETTE=1 firefox &`
  or `flatpak run org.mozilla.firefox -marionette`.
- **Readings include other Firefox windows** — the CPU/memory monitors
  aggregate *all* Firefox processes on the system, so close other
  instances for clean measurements.
- **No page load values (`Load: n/a`)** — normal while a page is still
  loading, or right after opening a blank tab.
