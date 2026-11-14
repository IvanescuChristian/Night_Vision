# SPDX-License-Identifier: LicenseRef-PolyForm-Strict-1.0.0
# Copyright (c) 2026 Christian Ivanescu. See LICENSE for terms (source-available; not open source).
"""Command line entry point: GUI by default, headless/demo/ctl for the rest.

Exit codes: 0 ok, 1 runtime failure or command rejected, 2 usage error,
3 GUI unavailable (no Tk/display), 4 no running instance for `ctl`,
5 another instance already owns the control socket (headless).
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from typing import Optional

from .core.presets import PRESETS

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_NO_GUI, EXIT_NO_INSTANCE, EXIT_BUSY = 0, 1, 2, 3, 4, 5
CTL_ACTIONS = ("toggle", "next-preset", "preset", "profile", "status", "quit")


def _monitor_arg(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"monitor must be a whole number, got {text!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError("monitor is 1-based: use 1 for the first monitor")
    return n


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nightvision",
        description="Display-side image enhancement for dark scenes. Never reads, hooks "
                    "or modifies any game.")
    p.add_argument("--mode", choices=("off", "display", "overlay", "preview"))
    p.add_argument("--preset", metavar="NAME")
    p.add_argument("--profile", metavar="NAME")
    p.add_argument("--headless", action="store_true", help="no GUI; run until SIGINT or `ctl quit`")
    p.add_argument("--list-backends", action="store_true")
    p.add_argument("--list-presets", action="store_true")
    p.add_argument("--demo", action="store_true",
                   help="synthetic dark scene in a preview window (no game needed)")
    p.add_argument("--gpu", choices=("auto", "gpu", "cpu"), default="auto")
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--monitor", type=_monitor_arg, default=1, metavar="N",
                   help="monitor to capture and cover, 1-based as the OS lists them (default 1)")
    p.add_argument("--config", metavar="PATH")
    p.add_argument("--dry-run", action="store_true", help="use the null tone backend")
    p.add_argument("--max-seconds", type=float, default=None, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command")
    c = sub.add_parser("ctl", help="control a running instance")
    c.add_argument("action", choices=CTL_ACTIONS)
    c.add_argument("name", nargs="?")
    return p


def _print_diagnostics(d: dict) -> None:
    pl = d["platform"]
    print(f"platform: {pl['os']} / {pl['session']} (desktop={pl['desktop'] or 'n/a'}, "
          f"wlroots={pl['wlroots']})")
    for key in ("tone", "capture", "process", "present"):
        s = d[key]
        print(f"{key}: {s.get('backend') or 'none'}"
              + (f" [{s['mode']}]" if s.get("mode") else ""))
        for r in s.get("reasons", []):
            print(f"    - {r}")
    print(f"gpu renderer: {d.get('gpu_renderer') or 'n/a'}")
    print("hotkeys: " + "; ".join(d["hotkeys"]["reasons"]))


def _ctl(args) -> int:
    from .control import send_command
    cmd = {"toggle": "toggle", "next-preset": "next_preset", "preset": "preset",
           "profile": "profile", "status": "status", "quit": "quit"}[args.action]
    msg: dict = {"cmd": cmd}
    if cmd in ("preset", "profile"):
        if not args.name:
            print(f"ctl {args.action} needs a NAME", file=sys.stderr)
            return EXIT_USAGE
        msg["name"] = args.name
    try:
        reply = send_command(msg)
    except ConnectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_INSTANCE
    if not reply.get("ok"):
        print(f"error: {reply.get('error')}", file=sys.stderr)
        return EXIT_FAIL
    if cmd == "status":
        print(json.dumps(reply.get("status", {}), indent=2))
    return EXIT_OK


def _configure_runtime() -> None:
    try:
        from .runtime import configure_runtime
    except ImportError:
        return
    configure_runtime()


def _owner_loop(app, max_seconds: Optional[float], want_pipeline: bool) -> int:
    """Shared headless/demo loop: pump until quit, timeout, or the pipeline dies."""
    end = None if max_seconds is None else time.monotonic() + max_seconds
    # <= 20 ms: GL window events and main-thread requests are serviced here.
    while not app.quit_requested.wait(0.01):
        app.pump()
        if want_pipeline and app.mode == "off":
            break  # pipeline failed or the preview window was closed
        if end is not None and time.monotonic() >= end:
            break
    return EXIT_FAIL if (want_pipeline and app.last_error) else EXIT_OK


def _install_stop_signals(app) -> list:
    """Make SIGINT/SIGTERM (SIGBREAK on Windows) request a clean stop.

    The handler only sets a flag; the owner loop (headless pump or the Tk tick)
    notices it and runs app.shutdown() on the main thread. Returns the names of
    the signals received, so the caller can report why it stopped.
    """
    received: list = []

    def stop(sig, _frm):
        received.append(signal.Signals(sig).name)
        app.quit_requested.set()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, stop)
            except (ValueError, OSError):
                pass
    return received


def _run_headless(app, args, mode: str) -> int:
    app.quit_requested.clear()
    _install_stop_signals(app)
    app.start_services()
    if app.control_error:
        print(f"error: control socket unavailable: {app.control_error}", file=sys.stderr)
        return EXIT_BUSY
    if mode != "off":
        app.set_mode(mode)
    print(f"nightvision running (mode={app.mode}, profile={app.profile}); "
          "stop with Ctrl+C or `nightvision ctl quit`", flush=True)
    return _owner_loop(app, args.max_seconds, False)


def _run_gui(app, args, mode: str) -> int:
    try:
        from .ui.main_window import MainWindow
        win = MainWindow(app)
    except Exception as exc:  # noqa: BLE001 - ImportError, TclError (no display)
        print(f"error: GUI unavailable ({exc}); try --headless", file=sys.stderr)
        return EXIT_NO_GUI
    # Tk's mainloop returns to Python on every pump timer tick (<= 50 ms), which is
    # when pending signal handlers run; the tick then sees quit_requested and closes.
    received = _install_stop_signals(app)
    app.start_services()
    if mode != "off":
        try:
            app.set_mode(mode)
        except Exception as exc:  # noqa: BLE001 - shown in the window too
            print(f"warning: {exc}", file=sys.stderr)
    if args.max_seconds is not None:
        win.root.after(int(args.max_seconds * 1000), win.close)
    win.run()
    if received:
        print(f"nightvision: stopped by {received[0]}; display restored", file=sys.stderr,
              flush=True)
    return EXIT_OK


def main(argv=None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else EXIT_USAGE
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.list_presets:
        for name in PRESETS:
            print(name)
        return EXIT_OK
    if args.command == "ctl":
        return _ctl(args)

    _configure_runtime()
    from .app import AppError, NightVisionApp
    try:
        app = NightVisionApp(args.config, dry_run=args.dry_run, gpu=args.gpu, fps=args.fps,
                             synthetic=args.demo, monitor=args.monitor,
                             enable_hotkeys=not args.demo and not args.list_backends,
                             enable_control=not args.demo and not args.list_backends)
        if args.preset:
            app.apply_preset(args.preset)
        if args.profile:
            app.set_profile(args.profile)
    except KeyError as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return EXIT_USAGE

    try:
        if args.list_backends:
            _print_diagnostics(app.diagnostics(probe=True))
            return EXIT_OK
        if args.demo:
            if not args.preset and not args.profile:
                app.apply_preset("night_vision")
            try:
                app.set_mode("preview")
            except Exception as exc:  # noqa: BLE001
                print(f"error: demo preview failed: {exc}", file=sys.stderr)
                return EXIT_FAIL
            rc = _owner_loop(app, args.max_seconds, True)
            if app.last_error:
                print(f"error: {app.last_error}", file=sys.stderr)
            return rc
        if args.monitor != 1:
            try:
                app.monitor_rect()  # fail early, naming the monitors that do exist
            except AppError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_USAGE
        mode = args.mode or ("display" if args.headless and (args.preset or args.profile)
                             else "off")
        if args.headless:
            return _run_headless(app, args, mode)
        return _run_gui(app, args, mode)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    finally:
        app.shutdown()


if __name__ == "__main__":
    sys.exit(main())
