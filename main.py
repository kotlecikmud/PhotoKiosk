#!/usr/bin/env python3
"""Unified CLI and runtime entry point for PhotoKiosk."""
import sys
import argparse
import uvicorn
from backend.database import init_database


def main():
    parser = argparse.ArgumentParser(description="PhotoKiosk Application & Services")
    parser.add_argument("--mode", choices=["all", "gui", "backend"], default="all",
                        help="Run mode: 'gui' for kiosk interface, 'backend' for API/Admin, 'all' for both.")
    parser.add_argument("--fullscreen", action="store_true", help="Launch GUI in fullscreen mode.")
    parser.add_argument("--host", default="0.0.0.0", help="Backend bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Backend bind port (default: 8080)")

    args = parser.parse_args()

    # Ensure database is initialized
    init_database()

    if args.mode == "backend":
        print(f"Starting PhotoKiosk Backend & Admin service on http://{args.host}:{args.port}")
        uvicorn.run("backend.app:app", host=args.host, port=args.port, reload=False)

    elif args.mode == "gui":
        print("Launching PhotoKiosk Touchscreen Interface...")
        from gui.app import run_gui
        run_gui(fullscreen=args.fullscreen)

    elif args.mode == "all":
        import multiprocessing
        print(f"Starting PhotoKiosk Backend on port {args.port}...")
        backend_proc = multiprocessing.Process(
            target=uvicorn.run,
            args=("backend.app:app",),
            kwargs={"host": args.host, "port": args.port, "reload": False},
            daemon=True
        )
        backend_proc.start()

        print("Launching PhotoKiosk GUI...")
        from gui.app import run_gui
        try:
            run_gui(fullscreen=args.fullscreen)
        finally:
            if backend_proc.is_alive():
                backend_proc.terminate()
                backend_proc.join()


if __name__ == "__main__":
    main()
