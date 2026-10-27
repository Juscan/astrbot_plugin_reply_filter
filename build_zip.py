"""Build an installable source ZIP using only Python's standard library."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "dist" / "astrbot_plugin_reply_filter-1.1.1.zip"
EXCLUDED = {
    ".git", ".venv", "venv", "__pycache__", ".ruff_cache", ".pytest_cache",
    "dist", "build", ".idea", ".vscode",
}


def build() -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            relative = path.relative_to(ROOT)
            if (
                not path.is_file() or path.is_symlink()
                or any(part in EXCLUDED for part in relative.parts)
                or path.suffix in {".pyc", ".pyo", ".zip", ".log"}
                or path.name == ".env" or path.name.startswith(".env.")
            ):
                continue
            archive.write(path, Path("astrbot_plugin_reply_filter") / relative)
    with ZipFile(OUTPUT, "r") as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise RuntimeError(f"ZIP validation failed: {corrupt}")
    return OUTPUT


if __name__ == "__main__":
    print(build())
