#!/usr/bin/env python3
"""
SMC Trading Suite — unified launcher
Run:  python3 start.py
Opens http://localhost:8080 with all three apps in tabs.
Press Ctrl+C to stop everything.
"""

import os, signal, subprocess, sys, time, webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))

SERVERS = [
    {
        "name":   "FX News + Hub",
        "script": os.path.join(ROOT, "server.py"),
        "cwd":    ROOT,
        "port":   8080,
    },
    {
        "name":   "SMC Backtest",
        "script": os.path.join(ROOT, "smc_backtest", "server.py"),
        "cwd":    os.path.join(ROOT, "smc_backtest"),
        "port":   8081,
    },
]

def start_servers():
    procs = []
    for s in SERVERS:
        p = subprocess.Popen(
            [sys.executable, s["script"]],
            cwd=s["cwd"],
        )
        procs.append((s["name"], p))
        print(f"  ✓ {s['name']} started (pid {p.pid})")
    return procs

def stop_servers(procs):
    print("\n  Stopping servers…")
    for name, p in procs:
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            p.kill()
        print(f"  ✗ {name} stopped")

def main():
    print("\n  SMC Trading Suite")
    print("  ─────────────────────────────────────────────────")
    procs = start_servers()

    # Give servers a moment to bind their ports
    time.sleep(1.2)

    url = "http://localhost:8080"
    print(f"\n  Opening {url} …\n")
    webbrowser.open(url)

    # Keep running until Ctrl+C
    try:
        for _, p in procs:
            p.wait()
    except KeyboardInterrupt:
        pass
    finally:
        stop_servers(procs)

if __name__ == "__main__":
    main()
