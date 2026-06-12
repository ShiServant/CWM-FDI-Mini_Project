'''
Mini Project of CWM-FDI
Project Structure:
Firefox Bottleneck Detector (Linux)

├── Metric Collector
│   ├── CPU monitor
│   ├── Memory monitor
│   ├── Page load timer
│   └── Responsiveness tester
│
├── Bottleneck Analyzer
│   ├── CPU rules
│   ├── Memory rules
│   └── Network rules
│
└── Report Generator

CPU Detection:

* Warning:

  * Average CPU usage > 80%
    OR
  * Average JavaScript responsiveness > 0.5s
* Bottleneck:

  * Average CPU usage > 80%
    AND
  * Average JavaScript responsiveness > 0.5s

Memory Detection:

* Warning:

  * Memory usage > 80% of total system memory
    OR
  * Memory growth > 500MB within a 15-second window
* Bottleneck:

  * Memory usage > 80% of total system memory
    AND
  * Memory growth > 500MB within a 15-second window

Network Detection:

* Possible Network Bottleneck:

  * Average CPU usage < 50%
    AND
  * Memory usage < 80% of total system memory
    AND
  * Average page load time > 5s

Requirements:

* Use a 15-second sliding window for all averages.
* Continuously monitor Firefox.
* Print timestamp, severity, bottleneck type, metric values, and recommendation.
* Support Warning and Bottleneck severity levels.


'''
