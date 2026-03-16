#!/usr/bin/env python3
"""
Mimestream Bridge — localhost:9876
Ouvre un email dans Mimestream via AppleScript quand le CRM Flease appelle /open
Usage :
  python3 mimestream_bridge.py           # démarrer le serveur
  python3 mimestream_bridge.py --install  # installer en LaunchAgent (démarrage auto)
  python3 mimestream_bridge.py --uninstall # désinstaller le LaunchAgent
"""

import os
import sys
import json
import subprocess
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 9876
PLIST_LABEL = "com.flease.mimestream-bridge"
PLIST_PATH  = os.path.expanduser(f"~/Library/LaunchAgents/{PLIST_LABEL}.plist")
SCRIPT_PATH = os.path.abspath(__file__)


class BridgeHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[bridge] {fmt % args}", flush=True)

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/ping":
            self.send_response(200)
            self.send_cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if parsed.path == "/open":
            subject = params.get("subject", [""])[0]
            from_   = params.get("from",    [""])[0]

            if subject:
                query = urllib.parse.quote(subject[:120])
                url = f"mimestream://search?q={query}"
                try:
                    subprocess.run(["open", url], timeout=10, check=False)
                    print(f"[bridge] ✅ Recherche Mimestream : {subject[:60]}", flush=True)
                except Exception as e:
                    print(f"[bridge] ⚠ open URL erreur : {e}", flush=True)

            self.send_response(200)
            self.send_cors()
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b'<html><body><script>window.close()</script></body></html>')
            return

        self.send_response(404)
        self.end_headers()


def install():
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>{SCRIPT_PATH}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/mimestream_bridge.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/mimestream_bridge.log</string>
</dict>
</plist>
"""
    os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
    with open(PLIST_PATH, "w") as f:
        f.write(plist)
    subprocess.run(["launchctl", "load", PLIST_PATH], check=True)
    print(f"✅ LaunchAgent installé : {PLIST_PATH}")
    print(f"   Le bridge démarrera automatiquement à chaque connexion.")
    print(f"   Pour vérifier : curl http://localhost:{PORT}/ping")


def uninstall():
    if os.path.exists(PLIST_PATH):
        subprocess.run(["launchctl", "unload", PLIST_PATH], check=False)
        os.remove(PLIST_PATH)
        print(f"✅ LaunchAgent supprimé.")
    else:
        print("⚠ Aucun LaunchAgent trouvé.")


if __name__ == "__main__":
    if "--install" in sys.argv:
        install()
    elif "--uninstall" in sys.argv:
        uninstall()
    else:
        print(f"🚀 Mimestream Bridge démarré sur http://localhost:{PORT}", flush=True)
        print(f"   /ping  — vérifie que le bridge tourne")
        print(f"   /open  — ouvre un email dans Mimestream")
        print(f"   Ctrl+C pour arrêter\n")
        server = HTTPServer(("127.0.0.1", PORT), BridgeHandler)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n🛑 Bridge arrêté.")
