#!/usr/bin/env python3
"""
Mimestream Bridge — Flease CRM
================================
Serveur local (localhost:9876) qui intercepte les clics "Ouvrir" du CRM
et ouvre l'email directement dans Mimestream.

USAGE :
  python3 mimestream_bridge.py

DÉMARRAGE AUTO (login item macOS) :
  1. Ouvre "Préférences Système" → "Général" → "Éléments de connexion"
  2. Clique sur "+" et sélectionne ce script (ou l'app créée avec launch_agent.sh)

OU via launchd (recommandé) :
  python3 mimestream_bridge.py --install    # installe le LaunchAgent
  python3 mimestream_bridge.py --uninstall  # désinstalle
"""

import sys
import subprocess
import urllib.parse
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 9876


def open_in_mimestream(thread_id: str, subject: str, from_addr: str):
    """Ouvre Mimestream et navigue vers l'email via search."""
    # 1. Active/ouvre Mimestream
    subprocess.Popen(['open', '-a', 'Mimestream'])
    time.sleep(0.6)

    # 2. Si on a un sujet, on fait une recherche dans Mimestream via AppleScript
    search_term = subject[:50] if subject else from_addr[:50] if from_addr else ''

    if search_term:
        # Nettoie les guillemets pour AppleScript
        safe_term = search_term.replace('"', "'").replace('\\', '')
        script = f'''
tell application "Mimestream"
    activate
end tell
delay 0.4
tell application "System Events"
    tell process "Mimestream"
        -- Cmd+F ou Cmd+Option+F pour ouvrir la recherche
        key code 3 using {{command down, option down}}
        delay 0.3
        keystroke "{safe_term}"
    end tell
end tell
'''
        try:
            subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                timeout=5
            )
        except (subprocess.TimeoutExpired, Exception):
            pass  # Mimestream est quand même ouvert


class MimestreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        # CORS headers pour permettre les appels depuis GitHub Pages
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()

        if parsed.path == '/open':
            thread_id = params.get('id', [''])[0]
            subject   = params.get('subject', [''])[0]
            from_addr = params.get('from', [''])[0]
            open_in_mimestream(thread_id, subject, from_addr)
            self.wfile.write(b'ok')
        elif parsed.path == '/ping':
            self.wfile.write(b'pong')
        else:
            self.wfile.write(b'Mimestream Bridge running')

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.end_headers()

    def log_message(self, fmt, *args):
        # Log épuré
        print(f'[bridge] {self.address_string()} — {fmt % args}')


def install_launch_agent():
    """Installe un LaunchAgent macOS pour démarrage auto."""
    import os, plistlib, pathlib

    script_path = pathlib.Path(__file__).resolve()
    python_path = sys.executable
    plist_path = pathlib.Path.home() / 'Library/LaunchAgents/com.flease.mimestream-bridge.plist'

    plist = {
        'Label': 'com.flease.mimestream-bridge',
        'ProgramArguments': [python_path, str(script_path)],
        'RunAtLoad': True,
        'KeepAlive': True,
        'StandardOutPath': str(pathlib.Path.home() / 'Library/Logs/mimestream-bridge.log'),
        'StandardErrorPath': str(pathlib.Path.home() / 'Library/Logs/mimestream-bridge-error.log'),
    }

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with open(plist_path, 'wb') as f:
        plistlib.dump(plist, f)

    os.system(f'launchctl load "{plist_path}"')
    print(f'✅ LaunchAgent installé : {plist_path}')
    print('   Le bridge démarrera automatiquement au prochain login.')
    print('   Pour le démarrer maintenant :')
    print(f'   launchctl start com.flease.mimestream-bridge')


def uninstall_launch_agent():
    import pathlib, os
    plist_path = pathlib.Path.home() / 'Library/LaunchAgents/com.flease.mimestream-bridge.plist'
    if plist_path.exists():
        os.system(f'launchctl unload "{plist_path}"')
        plist_path.unlink()
        print(f'✅ LaunchAgent désinstallé.')
    else:
        print('Aucun LaunchAgent trouvé.')


if __name__ == '__main__':
    if '--install' in sys.argv:
        install_launch_agent()
        sys.exit(0)
    if '--uninstall' in sys.argv:
        uninstall_launch_agent()
        sys.exit(0)

    print(f'🚀 Mimestream Bridge — http://localhost:{PORT}')
    print(f'   Ouvre Mimestream à chaque clic "Ouvrir" dans le CRM Flease.')
    print(f'   Installe le démarrage auto : python3 {__file__} --install')
    print(f'   Ctrl+C pour arrêter.\n')

    try:
        server = HTTPServer(('localhost', PORT), MimestreamHandler)
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n👋 Bridge arrêté.')
    except OSError as e:
        if 'Address already in use' in str(e):
            print(f'❌ Port {PORT} déjà utilisé — le bridge est peut-être déjà lancé.')
        else:
            raise
