"""Package a console executable and include the bundled runtime's license notices."""

import importlib.metadata
import ssl
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
dist = root / "dist"
dist.mkdir(exist_ok=True)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
pyinstaller = importlib.metadata.distribution("pyinstaller")
bootloader_license = next(
    pyinstaller.locate_file(p) for p in pyinstaller.files if str(p).endswith("licenses/COPYING.txt")
)
notices = dist / "THIRD_PARTY_NOTICES.txt"
parts = [
    "Rangeglass Windows executable: bundled third-party software notices.\n"
    "Rangeglass itself is MIT licensed. Python and its bundled libraries are unmodified.\n",
    f"Python {sys.version.split()[0]}\n" + python_license.read_text(encoding="utf-8"),
    f"PyInstaller {pyinstaller.version}\n" + bootloader_license.read_text(encoding="utf-8"),
    ssl.OPENSSL_VERSION + "\n" + (root / "docs/licenses/OpenSSL.txt").read_text(encoding="utf-8"),
]
notices.write_text("\n\n".join(parts), encoding="utf-8", newline="\n")
subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name",
        "rangeglass-windows-x64",
        "--add-data",
        "LICENSE;.",
        "--add-data",
        "dist/THIRD_PARTY_NOTICES.txt;.",
        "tools/frozen_entry.py",
    ],
    cwd=root,
    check=True,
)
