#!/usr/bin/env python3
"""Start nivraM on loopback and open the local browser application."""
import argparse
import sys
import webbrowser
import threading

def main():
    parser=argparse.ArgumentParser(description='nivraM local chemical editor')
    parser.add_argument('--port',type=int,default=0,help='Loopback port; zero chooses an available port')
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    if not 0<=args.port<=65535:parser.error('Port must be between 0 and 65535.')
    try:
        from nivram.server import make_server
        from nivram import __version__
        server=make_server(args.port)
    except ImportError as exc:
        print('nivraM dependencies are missing. Run SETUP-WINDOWS.cmd, or pip install -r requirements.txt.',file=sys.stderr)
        print(str(exc),file=sys.stderr);return 1
    except OSError as exc:
        print(f'Unable to open the local port: {exc}',file=sys.stderr);return 1
    address=f'http://127.0.0.1:{server.server_port}/'
    print(f'nivraM {__version__}\nOpen {address}\nKeep this window open. Press Ctrl+C to stop.\nCore editing stays local. PubChem receives a structure only after its request-specific confirmation.',flush=True)
    if not args.no_browser:
        threading.Timer(0.5,lambda:webbrowser.open(address)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:print('\nClosing nivraM.')
    finally:server.server_close()
    return 0
if __name__=='__main__':raise SystemExit(main())
