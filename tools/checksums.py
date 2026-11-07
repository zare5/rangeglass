"""Write SHA-256 checksums for the release assets in dist/."""

import hashlib
from pathlib import Path

directory = Path(__file__).resolve().parents[1] / "dist"
lines = []
for path in sorted(directory.iterdir()):
    if path.is_file() and path.name != "SHA256SUMS.txt":
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}\n")
(directory / "SHA256SUMS.txt").write_text("".join(lines), encoding="ascii", newline="\n")
print("".join(lines), end="")
