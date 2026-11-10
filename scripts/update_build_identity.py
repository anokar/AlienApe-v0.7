"""Regenerate the diagnostic source-manifest identifier from Git-normalized files."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = "src/alienape/build_identity.py"


def main() -> None:
    listed = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
    ).splitlines()
    paths = sorted(
        {
            path
            for path in listed
            if path
            and path.decode("utf-8") != IDENTITY_PATH
            and not path.decode("utf-8").startswith("work/")
        }
    )
    manifest = hashlib.sha256()
    for raw_path in paths:
        relative = raw_path.decode("utf-8")
        blob_id = subprocess.check_output(
            ["git", "hash-object", f"--path={relative}", "--", relative],
            cwd=ROOT,
        ).strip()
        manifest.update(raw_path)
        manifest.update(b"\0")
        manifest.update(blob_id)
        manifest.update(b"\n")

    build_id = f"sha256:{manifest.hexdigest()}"
    destination = ROOT / IDENTITY_PATH
    destination.write_text(
        '"""Git-normalized source manifest ID; excludes this file and local work/ artifacts."""\n'
        f'BUILD_ID = "{build_id}"\n',
        encoding="utf-8",
        newline="\n",
    )
    print(build_id)


if __name__ == "__main__":
    main()
