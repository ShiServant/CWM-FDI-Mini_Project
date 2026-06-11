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

Bottleneck Criterias:
CPU Bottleneck:
CPU Usage > 80% for at least 15 seconds
AND
Average Page Load Time > 3 seconds

Memory Bottleneck:
Memory Usage increases continuously
(e.g., > 500 MB growth within 5 minutes)
AND
Browser Responsiveness > 0.5 seconds

Network Bottleneck:
CPU Usage < 50%
AND
Memory Usage remains within normal range
AND
Average Page Load Time > 5 seconds

'''
