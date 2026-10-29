"""Collect real PubMed records and freeze file-level provenance, never medical labels."""

import hashlib
import json
from pathlib import Path
import tempfile

from .core import AgentError, utc_now


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_new(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation: published snapshots and experiment records are immutable.
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def create_manifest(path, sources, usage_note="Chưa xác định quyền tái phân phối; kiểm tra trước khi chia sẻ."):
    path = Path(path)
    documents = [json.loads(row["payload"]) for row in sources.store.db.execute("SELECT payload FROM documents")]
    return {
        "schema_version": "1.0", "created_at": utc_now(),
        "file": path.name, "sha256": file_sha256(path), "usage_note": usage_note,
        "indexed_chunks": len(documents),
        "original_documents": len({d.get("document_id") or d["id"] for d in documents}),
        "missing_content": sum(not d.get("content", "").strip() for d in documents),
        "missing_year": sum(d.get("year") is None for d in documents),
        "documents": [{k: doc.get(k) for k in ("id", "document_id", "pmid", "doi", "title", "year", "content_scope", "fetched_at", "provenance", "content_hash")} for doc in documents],
        "normalization": "medical_agent.sources: PubMed metadata/abstract; local chunks 2600 chars, overlap 200; FTS5/BM25",
        "clinical_validation": "not_performed",
    }


def verify_manifest(path):
    path = Path(path)
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    if not manifest_path.is_file():
        raise ValueError(f"Thiếu manifest: {manifest_path}. Chạy lệnh manifest trước khi benchmark.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("file") != path.name or manifest.get("sha256") != file_sha256(path):
        raise ValueError("Corpus khác manifest đã đóng băng; tạo snapshot phiên bản mới.")
    return manifest


def freeze_manifest(path, usage_note="Chưa xác định quyền tái phân phối; kiểm tra trước khi chia sẻ."):
    from .sources import Sources
    from .store import Store
    with tempfile.TemporaryDirectory() as directory:
        store = Store(directory)
        try:
            sources = Sources(store)
            sources.ingest(path)
            manifest = create_manifest(path, sources, usage_note)
        finally:
            store.close()
    write_json_new(str(path) + ".manifest.json", manifest)
    return manifest


def collect_pubmed(sources, queries, output, limit=20, start_year=None, end_year=None):
    """Every record comes from EFetch. Incomplete collection is explicitly marked."""
    output = Path(output)
    if output.exists() or output.with_suffix(output.suffix + ".manifest.json").exists():
        raise ValueError("Snapshot đã tồn tại; dùng tên phiên bản mới.")
    records, searches, errors = {}, [], []
    for query in queries:
        result = sources.search_pubmed(query, {"start_year": start_year, "end_year": end_year}, limit=limit)
        searches.append({k: result.get(k) for k in ("query", "query_translation", "warnings", "total_matches", "fetched_at", "source_mode")})
        for candidate in result["candidates"]:
            if candidate["id"] in records:
                continue
            try:
                doc = sources.read_source(candidate["id"])
            except AgentError as exc:
                # A block must stop rather than hammering the upstream service.
                from .core import SourceBlocked
                if isinstance(exc, SourceBlocked):
                    raise
                errors.append({"source_id": candidate["id"], "error": str(exc)})
                continue
            records[doc["id"]] = {"pmid": doc["pmid"], "title": doc["title"], "publication_year": doc["year"],
                                   "abstract": doc["content"], "doi": doc.get("doi"), "fetched_at": doc.get("fetched_at")}
    payload = {"schema_version": "1.0", "fetched_at": utc_now(), "searches": searches,
               "status": "partial" if errors else "collected_not_reviewed", "errors": errors,
               "records": list(records.values())}
    if not records:
        raise AgentError("Không thu thập được bản ghi nào; không tạo snapshot rỗng để đánh giá.")
    write_json_new(output, payload)
    return payload
