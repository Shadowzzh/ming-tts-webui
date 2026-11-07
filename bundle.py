#!/usr/bin/env python3
"""Build a self-contained release from this repository's whitelisted files."""
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
    parser.add_argument("--log-dir", type=Path, help="Frontend build log directory (default: system temporary directory)")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    logs = args.log_dir or Path(tempfile.gettempdir()) / "ming-tts-webui-build-logs"
    if not args.skip_build:
        logs.mkdir(parents=True, exist_ok=True)
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
    files.update({name: source / name for name in (
        "LICENSE", "README.md", "Dockerfile", "compose.yaml", ".dockerignore",
        "requirements-web.txt", "requirements-model.txt", "docs/ray.md", "docs/参数说明.md",
    )})
    try:
        version = json.loads((source / "frontend/package.json").read_text())["version"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise SystemExit(f"Cannot read application version from frontend/package.json: {error}") from error
    if not isinstance(version, str) or not version.strip():
        raise SystemExit("frontend/package.json must declare a non-empty version")
    with tempfile.TemporaryDirectory(prefix="ming-bundle-") as directory:
        staging = Path(directory)
        environment = (source / ".env.example").read_text()
        if "WEB_ASSETS=frontend" not in environment:
            raise SystemExit(".env.example must declare WEB_ASSETS=frontend")
        (staging / ".env.example").write_text(environment.replace("WEB_ASSETS=frontend", "WEB_ASSETS=bundled"))
        files[".env.example"] = staging / ".env.example"
        manifest = {"version": version, "upstream": "Kodeer/ming-tts-web@630933be275c5f5c60234f13ddd475848a2f741d",
                    "created_at": datetime.now().astimezone().isoformat(),
                    "sha256": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}}
        path = staging / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        files["manifest.json"] = path
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(args.output, "w:gz") as archive:
            for name, file in files.items():
                archive.add(file, arcname=name, recursive=False)
    print(f"Release: {args.output}")
    print(f"SHA256: {hashlib.sha256(args.output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
