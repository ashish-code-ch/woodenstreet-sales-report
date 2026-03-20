#!/usr/bin/env python3
"""
Starts HTTP server + localhost.run tunnel and prints the public URL.
Run: python3 serve.py
"""
import subprocess, threading, time, re, sys, os, signal

PORT = 8080
EXPORTS_DIR = "/home/user/Documents/AI/SalesScorecard/exports"

def start_http_server():
    import urllib.request
    try:
        urllib.request.urlopen(f'http://localhost:{PORT}/', timeout=2)
        print(f"[HTTP] Server already running on port {PORT}")
        return None
    except:
        pass
    proc = subprocess.Popen(
        [sys.executable, '-m', 'http.server', str(PORT)],
        cwd=EXPORTS_DIR,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(1)
    print(f"[HTTP] Server started (PID {proc.pid}) on port {PORT}")
    return proc

def start_tunnel():
    proc = subprocess.Popen(
        ['ssh', '-o', 'StrictHostKeyChecking=no',
         '-o', 'ServerAliveInterval=20',
         '-o', 'ServerAliveCountMax=99',
         '-o', 'LogLevel=VERBOSE',
         '-R', f'80:localhost:{PORT}',
         'nokey@localhost.run'],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True, bufsize=1
    )
    print(f"[TUNNEL] Started SSH tunnel (PID {proc.pid})")
    return proc

def read_url(proc, timeout=30):
    deadline = time.time() + timeout
    url = None
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.2)
            continue
        clean = re.sub(r'\x1b\[[0-9;=]*[mGKHF]', '', line).strip()
        if clean:
            print(f"[TUNNEL] {clean}")
        m = re.search(r'https://([a-f0-9]+\.lhr\.life)', clean)
        if m:
            url = f"https://{m.group(1)}"
            break
    return url

http_proc = start_http_server()
tunnel_proc = start_tunnel()

print("\n[TUNNEL] Waiting for public URL...\n")
url = read_url(tunnel_proc, timeout=40)

if url:
    report_url = url + "/report.html"
    print("\n" + "="*55)
    print("  ✅  REPORT IS LIVE")
    print("="*55)
    print(f"\n  🌐  {report_url}\n")
    print("="*55)
    print("  Share this link — open in any browser.")
    print("  Press Ctrl+C to stop the server.\n")

    # Save URL to file
    with open("/tmp/live_url.txt", "w") as f:
        f.write(report_url + "\n")

    # Keep alive and restart tunnel if it drops
    def keep_alive():
        while True:
            time.sleep(5)
            if tunnel_proc.poll() is not None:
                print("\n[TUNNEL] Reconnecting...")
                new_proc = start_tunnel()
                new_url = read_url(new_proc, timeout=40)
                if new_url:
                    print(f"\n  NEW URL: {new_url}/report.html\n")

    t = threading.Thread(target=keep_alive, daemon=True)
    t.start()

    try:
        tunnel_proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        tunnel_proc.terminate()
        if http_proc:
            http_proc.terminate()
else:
    print("\n[ERROR] Could not get public URL from localhost.run")
    print("The local server is still running at http://10.0.10.12:8080/report.html")
    print("Check your network or try again.")
