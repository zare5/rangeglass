"""Exercise an installed package, zipapp, or executable away from the source tree."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--zipapp", type=Path)
parser.add_argument("--executable", type=Path)
args = parser.parse_args()
if args.executable:
    command = [str(args.executable.resolve())]
elif args.zipapp:
    command = [sys.executable, str(args.zipapp.resolve())]
else:
    command = [sys.executable, "-m", "rangeglass"]

with tempfile.TemporaryDirectory(prefix="rangeglass-smoke-") as directory:
    for mode, verdict, code in [("good", "pass", 0), ("broken", "fail", 1)]:
        result = subprocess.run(
            [*command, "--demo", mode, "--json"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=30,
        )
        report = json.loads(result.stdout)
        if result.returncode != code or report["verdict"] != verdict:
            raise SystemExit(f"Smoke failed: {mode}, exit={result.returncode}, report={report}")
        if report["bytes_read"] > 7175 or report["schema_version"] != 1:
            raise SystemExit("Unexpected byte budget or report schema.")
        print(f"{mode}: {verdict}, exit {code}, {report['bytes_read']} bytes")
