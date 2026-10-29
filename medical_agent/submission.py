"""Package reviewable source artifacts; exclude all runtime data and secrets by allowlist."""

import argparse
import json
from pathlib import Path
import zipfile

from .core import utc_now
from .corpus import file_sha256


def build_bundle(repository, output):
    repository, output = Path(repository).resolve(), Path(output)
    roots = [repository / name for name in ("medical_agent", "lessons", "tests", "docs", "evals")]
    files = [repository / "README.md"]
    for root in roots:
        files.extend(p for p in root.rglob("*") if p.is_file() and p.suffix in {".py", ".md", ".json", ".html", ".css", ".js", ".svg"} and "__pycache__" not in p.parts)
    files = sorted(set(files))
    if any(p.is_symlink() or not p.resolve().is_relative_to(repository) for p in files):
        raise ValueError("Gói nộp không nhận symlink hoặc file ngoài repository.")
    if output.resolve().is_relative_to(repository / "medical_agent"):
        raise ValueError("Chọn đường dẫn gói nộp trong outputs hoặc ngoài mã nguồn.")
    manifest = {"schema_version": "1.0", "created_at": utc_now(), "scope": "source_and_documentation_only",
                "runtime_data_included": False, "clinical_validation": "not_performed",
                "files": [{"path": str(p.relative_to(repository)), "sha256": file_sha256(p), "bytes": p.stat().st_size} for p in files]}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, str(path.relative_to(repository)))
        archive.writestr("SUBMISSION-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {"path": str(output), "files": len(files), "sha256": file_sha256(output), "runtime_data_included": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Đóng gói mã nguồn và tài liệu; loại dữ liệu runtime và secrets.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build_bundle(Path(__file__).resolve().parents[1], args.output), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Lỗi đóng gói: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
