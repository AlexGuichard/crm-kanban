#!/usr/bin/env python3
"""
Bridge local Apple Reminders — port 9878
Crée des rappels dans l'app Rappels Mac (sync iCloud → iPhone)
Usage: python3 apple_reminders_bridge.py [--install] [--uninstall]
"""

import subprocess
import json
import sys
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

PORT = 9878
LAUNCHAGENT_LABEL = "com.flease.reminders-bridge"
LAUNCHAGENT_PATH  = os.path.expanduser(f"~/Library/LaunchAgents/{LAUNCHAGENT_LABEL}.plist")
SCRIPT_PATH       = os.path.abspath(__file__)


def create_reminder(title, notes="", due_date=None, due_time=None, list_name="Rappels"):
    """Crée un rappel Apple via AppleScript."""

    # Construction date/heure
    alarm_script = ""
    if due_date:
        try:
            if due_time:
                dt_str = f"{due_date} {due_time}"
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
            else:
                dt = datetime.strptime(due_date, "%Y-%m-%d").replace(hour=9, minute=0)

            # Format AppleScript : "31/03/2026 09:00:00"
            as_date = dt.strftime("%d/%m/%Y %H:%M:%S")
            alarm_script = f'set due date of newReminder to date "{as_date}"'
            if due_time:
                alarm_script += f'\n      set remind me date of newReminder to date "{as_date}"'
        except Exception as e:
            print(f"[reminder] Erreur date: {e}")

    notes_escaped = notes.replace('"', '\\"').replace("\\", "\\\\")
    title_escaped = title.replace('"', '\\"').replace("\\", "\\\\")

    script = f"""
tell application "Reminders"
    tell list "{list_name}"
        set newReminder to make new reminder with properties {{name:"{title_escaped}", body:"{notes_escaped}"}}
        {alarm_script}
    end tell
end tell
"""
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        # Retry with default list if named list doesn't exist
        script_default = f"""
tell application "Reminders"
    set newReminder to make new reminder with properties {{name:"{title_escaped}", body:"{notes_escaped}"}}
    {alarm_script}
end tell
"""
        result = subprocess.run(["osascript", "-e", script_default], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}")

    def send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        get = lambda k, d="": params.get(k, [d])[0]

        if parsed.path == "/ping":
            self.send_json(200, {"status": "ok", "service": "apple-reminders-bridge"})

        elif parsed.path == "/remind":
            title    = get("title")
            notes    = get("notes", "")
            due_date = get("date", "")   # YYYY-MM-DD
            due_time = get("time", "")   # HH:MM
            list_name = get("list", "Rappels")

            if not title:
                self.send_json(400, {"error": "title requis"})
                return
            try:
                create_reminder(title, notes, due_date or None, due_time or None, list_name)
                print(f"[reminder] ✅ Créé : {title} ({due_date} {due_time})")
                self.send_json(200, {"status": "ok", "title": title})
            except Exception as e:
                print(f"[reminder] ❌ Erreur : {e}")
                self.send_json(500, {"error": str(e)})

        else:
            self.send_json(404, {"error": "route inconnue"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        try:
            data = json.loads(body)
        except Exception:
            self.send_json(400, {"error": "JSON invalide"})
            return

        if self.path == "/remind":
            title     = data.get("title", "")
            notes     = data.get("notes", "")
            due_date  = data.get("date", "")
            due_time  = data.get("time", "")
            list_name = data.get("list", "Rappels")

            if not title:
                self.send_json(400, {"error": "title requis"})
                return
            try:
                create_reminder(title, notes, due_date or None, due_time or None, list_name)
                self.send_json(200, {"status": "ok", "title": title})
            except Exception as e:
                self.send_json(500, {"error": str(e)})
        else:
            self.send_json(404, {"error": "route inconnue"})


def install_launchagent():
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LAUNCHAGENT_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{sys.executable}</string>
    <string>{SCRIPT_PATH}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{os.path.expanduser("~/Library/Logs/flease-reminders.log")}</string>
  <key>StandardErrorPath</key>
  <string>{os.path.expanduser("~/Library/Logs/flease-reminders-err.log")}</string>
</dict>
</plist>"""
    os.makedirs(os.path.dirname(LAUNCHAGENT_PATH), exist_ok=True)
    with open(LAUNCHAGENT_PATH, "w") as f:
        f.write(plist)
    subprocess.run(["launchctl", "load", LAUNCHAGENT_PATH], check=True)
    print(f"✅ LaunchAgent installé : {LAUNCHAGENT_PATH}")
    print(f"   Le bridge démarrera automatiquement à chaque session sur le port {PORT}")


def uninstall_launchagent():
    subprocess.run(["launchctl", "unload", LAUNCHAGENT_PATH], check=False)
    if os.path.exists(LAUNCHAGENT_PATH):
        os.remove(LAUNCHAGENT_PATH)
    print("✅ LaunchAgent désinstallé")


if __name__ == "__main__":
    if "--install" in sys.argv:
        install_launchagent()
    elif "--uninstall" in sys.argv:
        uninstall_launchagent()
    else:
        print(f"🍎 Apple Reminders Bridge — port {PORT}")
        print(f"   GET /ping          → test de connexion")
        print(f"   GET /remind?title=...&date=YYYY-MM-DD&time=HH:MM")
        print(f"   POST /remind       → {{title, notes, date, time, list}}")
        print(f"   Installe en démarrage auto : python3 {SCRIPT_PATH} --install\n")
        server = HTTPServer(("127.0.0.1", PORT), Handler)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nArrêt du bridge.")
