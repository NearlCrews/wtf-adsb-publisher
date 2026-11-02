"""Dispatch publisher jobs with their original arguments and exit status."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOBS = {
    "publish": "run_autopilot.py",
    "write": "run_public_writer.py",
    "weekly": "run_weekly.py",
}
HELP = """Usage: publisher {publish|write|weekly|admin} [arguments]

publish   Publish eligible observations and deploy the checked site.
write     Write supported stories within the shared AI budget.
weekly    Research weekly features within the shared AI budget.
admin     Run the publisher administration CLI.

Each command accepts --help. With no command, this help is displayed.
"""


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"--help", "-h", "help"}:
        print(HELP, end="")
        return 0
    name, *arguments = args
    if name not in {*JOBS, "admin"}:
        print(f"Unknown publisher command: {name}", file=sys.stderr)
        return 2
    os.umask(0o077)
    if name in JOBS and not any(value in {"--help", "-h"} for value in arguments):
        hold = os.environ.get("PUBLISHER_ACTIVATION_HOLD")
        if hold:
            path = Path(hold)
            if not path.is_absolute():
                print("PUBLISHER_ACTIVATION_HOLD must be an absolute path.", file=sys.stderr)
                return 2
            try:
                path.lstat()
            except FileNotFoundError:
                pass
            except OSError:
                print("Cannot check the publisher activation hold; job refused.", file=sys.stderr)
                return 75
            else:
                print("Publisher activation is on hold; job refused.", file=sys.stderr)
                return 75
    if name == "admin":
        command = [str(Path(sys.executable).with_name("wtf-adsb-publisher")), *arguments]
    else:
        command = [sys.executable, str(ROOT / "tools" / JOBS[name]), *arguments]
    os.execv(command[0], command)


if __name__ == "__main__":
    raise SystemExit(main())
