'''
Diagnostic for the Marionette connection used by Detector.py.

Run on Linux:   python3 Diagnose.py

Tests every link in the chain needed for per-website attribution:
    1. Is Firefox running, and was it launched with -marionette?
    2. Is anything listening on port 2828?
    3. Does the Marionette handshake work?
    4. Can we run JavaScript in the page (responsiveness/page load)?
    5. Can we run the privileged per-site API (top consumers)?
'''

import json
import shutil
import socket

import psutil

from Marionette import MarionetteClient


FAILURES = []


def step(num, title):
    print(f"\n[{num}] {title}")


def ok(msg):
    print(f"    PASS  {msg}")


def fail(msg, hint=None):
    FAILURES.append(msg)
    print(f"    FAIL  {msg}")
    if hint:
        print(f"          Hint: {hint}")


def main():
    print("Marionette diagnostic")
    print("=" * 60)

    # ---- 1. Firefox processes and launch flags ----
    step(1, "Firefox processes and launch flags")
    firefox_procs = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if "firefox" in (p.info["name"] or "").lower():
                firefox_procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    if not firefox_procs:
        fail(
            "No Firefox process is running.",
            "Start it with:  firefox -marionette &",
        )
    else:
        ok(f"{len(firefox_procs)} Firefox process(es) found.")
        main_procs = [
            p for p in firefox_procs
            if "-contentproc" not in " ".join(p.info["cmdline"] or [])
        ]
        flagged = [
            p for p in main_procs
            if "-marionette" in " ".join(p.info["cmdline"] or [])
        ]
        for p in main_procs[:3]:
            print(f"          pid {p.pid}: {' '.join(p.info['cmdline'] or [])[:100]}")
        if flagged:
            ok("Main process command line contains -marionette.")
        else:
            fail(
                "No main Firefox process has -marionette in its command line.",
                "Firefox was already running when you launched it, or a "
                "wrapper dropped the flag. Run:  pkill firefox; then "
                "firefox -marionette -remote-allow-system-access &",
            )

    print(f"          firefox binary: {shutil.which('firefox') or 'not on PATH'}")

    # ---- 2. Port 2828 ----
    step(2, "TCP port 2828")
    try:
        probe = socket.create_connection(("127.0.0.1", 2828), timeout=3)
        probe.close()
        ok("Something is listening on 127.0.0.1:2828.")
    except OSError as exc:
        fail(
            f"Cannot connect to 127.0.0.1:2828 ({exc}).",
            "Marionette is not enabled in the running Firefox. "
            "Fix step 1 first; the remaining steps will be skipped.",
        )
        return

    # ---- 3. Marionette handshake ----
    step(3, "Marionette handshake + session")
    try:
        client = MarionetteClient()
        ok("Handshake and WebDriver:NewSession succeeded.")
    except Exception as exc:
        fail(f"{exc.__class__.__name__}: {exc}")
        return

    # ---- 4. Content-context JavaScript ----
    step(4, "JavaScript in the current page")
    try:
        title = client.execute_script("return document.title;")
        ok(f"execute_script works (page title: {title!r}).")
    except Exception as exc:
        fail(f"{exc.__class__.__name__}: {exc}")

    # ---- 5. Privileged per-site API ----
    step(5, "Privileged per-site API (ChromeUtils.requestProcInfo)")
    script = """
        const done = arguments[arguments.length - 1];
        ChromeUtils.requestProcInfo().then(info => {
            done(JSON.stringify(info.children.map(c => ({
                pid: c.pid, type: c.type, origin: c.origin || ''
            }))));
        }).catch(e => done(JSON.stringify({error: String(e)})));
    """
    try:
        client.set_context("chrome")
        try:
            raw = client.execute_async_script(script)
        finally:
            client.set_context("content")
        data = json.loads(raw)
        if isinstance(data, dict):
            fail(f"Script error inside Firefox: {data.get('error')}")
        else:
            ok(f"requestProcInfo works ({len(data)} child processes).")
            sites = sorted({d["origin"] for d in data if d["origin"]})
            if sites:
                print(f"          Site origins visible: {', '.join(sites[:5])}")
            else:
                print(
                    "          No site origins yet - open a few websites "
                    "and run again."
                )
    except Exception as exc:
        hint = None
        if "system access" in str(exc).lower():
            hint = (
                "Restart Firefox with:  pkill firefox; "
                "firefox -marionette -remote-allow-system-access &"
            )
        fail(f"{exc.__class__.__name__}: {exc}", hint)

    client.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} step(s) FAILED - fix the first failure "
              "above and re-run.")
    else:
        print("All steps passed -> Detector.py will show website names.")


if __name__ == "__main__":
    main()
