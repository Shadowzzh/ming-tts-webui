#!/usr/bin/env python3
"""Build a whitelisted release bundle; never copies the surrounding Ops worktree."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true", help="Use an already verified frontend/dist")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    config = source.parents[1] / "hosts/workhorse/ming-tts-web"
    logs = Path.home() / ".pi/agent/user-output" / datetime.now().strftime("%Y-%m") / "ming-tts-studio"
    logs.mkdir(parents=True, exist_ok=True)
    if not args.skip_build:
        for command, name in [(["npm", "ci"], "dependencies"), (["npm", "run", "build"], "build")]:
            path = logs / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.log"
            with path.open("w") as log:
                subprocess.run(command, cwd=source / "frontend", stdout=log, stderr=subprocess.STDOUT,
                               check=True, timeout=600)
            print(f"Validated: {path}")
    if not (source / "frontend/dist/index.html").is_file():
        raise SystemExit("Missing frontend/dist/index.html; build first")
    files = {f"app/{p.name}": p for p in sorted((source / "app").glob("*.py"))}
    files.update({f"static/{p.relative_to(source / 'frontend/dist')}": p
                  for p in sorted((source / "frontend/dist").rglob("*")) if p.is_file()})
    files.update({name: source / name for name in ("LICENSE", "requirements-web.txt")})
    files.update({name: config / name for name in ("Dockerfile", "compose.yaml")})
    manifest = {"version": "2.1.0", "upstream": "Kodeer/ming-tts-web@630933be275c5f5c60234f13ddd475848a2f741d",
                "created_at": datetime.now().astimezone().isoformat(),
                "sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ming-bundle-") as directory:
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        files["manifest.json"] = path
        with tarfile.open(args.output, "w:gz") as archive:
            for name, file in files.items():
                archive.add(file, arcname=name, recursive=False)
    print(f"Release: {args.output}")
    print(f"SHA256: {hashlib.sha256(args.output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
