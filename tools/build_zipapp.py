"""Build a deterministic, dependency-free zipapp containing only runtime code."""

import io
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
destination = root / "dist" / "rangeglass.pyz"
destination.parent.mkdir(exist_ok=True)
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    entries = {"__main__.py": b"from rangeglass.cli import main\nraise SystemExit(main())\n"}
    entries["LICENSE"] = (root / "LICENSE").read_bytes()
    for path in sorted((root / "rangeglass").glob("*.py")):
        entries[path.relative_to(root).as_posix()] = path.read_bytes()
    for name, content in sorted(entries.items()):
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, content)
destination.write_bytes(buffer.getvalue())
print(destination.name)
