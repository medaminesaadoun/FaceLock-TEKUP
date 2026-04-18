# main.py
import argparse
import getpass
import subprocess
import sys
from pathlib import Path


def _pythonw() -> str:
    return str(Path(sys.executable).parent / "pythonw.exe")


def _here() -> Path:
    return Path(__file__).parent


def _schtask_create(name: str, cmd: str, delay_minutes: int = 0) -> None:
    args = [
        "schtasks", "/create", "/f",
        "/tn", name,
        "/tr", cmd,
        "/sc", "ONLOGON",
        "/ru", getpass.getuser(),
    ]
    if delay_minutes:
        args += ["/delay", f"0:{delay_minutes:02d}"]
    subprocess.run(args, check=True, capture_output=True)


def _schtask_delete(name: str) -> None:
    subprocess.run(
        ["schtasks", "/delete", "/f", "/tn", name],
        check=False, capture_output=True,
    )


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------

def cmd_install(_args) -> None:
    pythonw = _pythonw()
    here = _here()

    tasks = [
        ("FaceLock-CoreService", f'"{pythonw}" "{here / "core_service.py"}"', 0),
        ("FaceLock-ModeA",       f'"{pythonw}" "{here / "main.py"}" mode-a',  1),
        ("FaceLock-ModeC1",      f'"{pythonw}" "{here / "main.py"}" mode-c1', 2),
    ]

    for name, cmd, delay in tasks:
        _schtask_create(name, cmd, delay)
        print(f"  registered: {name}")
    print("FaceLock scheduled tasks installed.")


def cmd_uninstall(_args) -> None:
    for name in ("FaceLock-CoreService", "FaceLock-ModeA", "FaceLock-ModeC1"):
        _schtask_delete(name)
        print(f"  removed: {name}")
    print("FaceLock scheduled tasks removed.")


# ---------------------------------------------------------------------------
# runtime entry points
# ---------------------------------------------------------------------------

def cmd_service(_args) -> None:
    from core_service import run
    run()


def cmd_enroll(_args) -> None:
    from ui.enrollment_window import launch
    launch()


def cmd_mode_a(_args) -> None:
    from modules.system_controller import run_mode_a
    run_mode_a()


def cmd_mode_b(args) -> None:
    from modules.system_controller import run_mode_b
    if not args.app:
        print("mode-b requires --app <executable>")
        sys.exit(1)
    ok = run_mode_b(args.app)
    sys.exit(0 if ok else 1)


def cmd_mode_c1(_args) -> None:
    from modules.system_controller import run_mode_c1
    run_mode_c1()


def cmd_tray(_args) -> None:
    from ui.status_indicator import launch
    launch()


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def cmd_launch(_args) -> None:
    """Default: start core service + tray, show enrollment wizard if needed."""
    import getpass
    import config
    from modules.gdpr import has_consent

    pythonw = _pythonw()
    here = _here()

    # Start core service as a detached background process
    subprocess.Popen(
        [pythonw, str(here / "core_service.py")],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    # Show enrollment wizard if this user has not consented yet
    if not has_consent(config.DB_PATH, getpass.getuser()):
        from ui.enrollment_window import launch as launch_enroll
        launch_enroll()

    # Start tray (blocks until quit)
    from ui.status_indicator import launch as launch_tray
    launch_tray()


def main() -> None:
    parser = argparse.ArgumentParser(prog="facelock", description="FaceLock — GDPR-compliant facial authentication")
    sub = parser.add_subparsers(dest="command", required=False)

    sub.add_parser("service",   help="Start the core service (pipe server + camera)")
    sub.add_parser("enroll",    help="Open the enrollment wizard")
    sub.add_parser("tray",      help="Start the system tray status indicator")
    sub.add_parser("mode-a",    help="Mode A — session locker")
    sub.add_parser("mode-c1",   help="Mode C1 — post-login startup gate")
    sub.add_parser("install",   help="Install Windows Task Scheduler tasks")
    sub.add_parser("uninstall", help="Remove Windows Task Scheduler tasks")

    b = sub.add_parser("mode-b", help="Mode B — app guard")
    b.add_argument("app", nargs="*", help="Command to launch after auth")

    args = parser.parse_args()
    command = args.command or "launch"
    {
        "launch":    cmd_launch,
        "service":   cmd_service,
        "enroll":    cmd_enroll,
        "tray":      cmd_tray,
        "mode-a":    cmd_mode_a,
        "mode-b":    cmd_mode_b,
        "mode-c1":   cmd_mode_c1,
        "install":   cmd_install,
        "uninstall": cmd_uninstall,
    }[command](args)


if __name__ == "__main__":
    main()
