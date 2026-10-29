"""Build a VPS archive using a source allowlist, without runtime data or secrets."""

import argparse
from pathlib import Path
import tarfile


def build_archive(repository, output):
    repository = Path(repository).resolve()
    files = [repository / "README.md"]
    for directory, pattern in (("medical_agent", "*.py"), ("lessons", "*.py"), ("tests", "*.py"), ("docs", "*.md")):
        files.extend((repository / directory).rglob(pattern))
    files.extend(repository / "evals" / name
                 for name in ("questions.json", "research_cases.json", "safety_cases.json", "run_safety.py"))
    files.extend(repository / "medical_agent" / "frontend" / name
                 for name in ("index.html", "styles.css", "app.js", "favicon.svg"))
    files.extend(repository / "deploy" / name
                 for name in ("package.py", "install.sh", "medisearch.service", "Caddyfile"))
    files = sorted(set(files))
    if any(path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(repository) for path in files):
        raise ValueError("Gói triển khai không nhận symlink hoặc file ngoài repository.")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    def source_metadata(info):
        info.uid = info.gid = 0
        info.uname = info.gname = "root"
        info.mode = 0o644
        return info

    with output.open("xb") as destination:
        with tarfile.open(fileobj=destination, mode="w:gz") as archive:
            for path in files:
                archive.add(path, arcname=str(path.relative_to(repository)), recursive=False, filter=source_metadata)
    return len(files)


def main():
    parser = argparse.ArgumentParser(description="Đóng gói nguồn để triển khai VPS; không chứa dữ liệu runtime.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        count = build_archive(Path(__file__).resolve().parents[1], args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Không tạo được gói: {exc}\n")
    print(f"Đã đóng gói {count} file vào {args.output}.")


if __name__ == "__main__":
    main()
