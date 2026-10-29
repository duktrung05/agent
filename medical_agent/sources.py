"""Read-only tools: PubMed plus a local SQLite FTS5/BM25 corpus."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .core import AgentError, SourceBlocked, utc_now


def text_of(element):
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def normalize_document(record, fetched_at=None, imported=False):
    pmid = record.get("pmid")
    if not isinstance(pmid, str) or not pmid.isascii() or not pmid.isdecimal() or int(pmid) <= 0:
        raise ValueError("Bản ghi PubMed thiếu PMID dạng chuỗi số dương.")
    year = record.get("publication_year", record.get("year"))
    year = int(year) if str(year).isdecimal() and len(str(year)) == 4 else None
    content = record.get("abstract") or record.get("content") or ""
    if not isinstance(content, str):
        raise ValueError("Abstract phải là chuỗi hoặc null.")
    if not isinstance(record.get("title") or "", str) or (record.get("doi") is not None and not isinstance(record["doi"], str)):
        raise ValueError("Tiêu đề và DOI phải là chuỗi hoặc thiếu.")
    if record.get("fetched_at") is not None and not isinstance(record["fetched_at"], str):
        raise ValueError("fetched_at phải là timestamp dạng chuỗi hoặc thiếu.")
    return {
        "id": "pubmed:" + pmid, "pmid": pmid, "title": record.get("title") or "",
        "year": year, "doi": record.get("doi"), "content": content,
        "content_scope": "metadata_and_abstract" if content else "metadata_only",
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "origin": "pubmed",
        "fetched_at": record.get("fetched_at") or fetched_at,
        "provenance": "user_imported_snapshot" if imported else "pubmed_api",
    }


class Sources:
    def __init__(self, store):
        self.store = store
        self.last_request = 0.0

    def _request(self, endpoint, parameters, output, timeout=30):
        deadline = time.monotonic() + timeout
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/" + endpoint + "?" + urlencode({**parameters, "tool": "medical_agent_local"})
        request = Request(url, headers={"User-Agent": "medical-agent-local/0.1", "Accept": "application/json" if output == "json" else "application/xml"})
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AgentError("Hết thời gian gọi PubMed.")
            spacing = max(0, 0.4 - (time.monotonic() - self.last_request))
            if spacing >= remaining:
                raise AgentError("Không còn thời gian chờ rate limit PubMed.")
            time.sleep(spacing)
            self.last_request = time.monotonic()
            try:
                with urlopen(request, timeout=max(0.1, deadline - time.monotonic())) as response:
                    raw = response.read()
                    content_type = response.headers.get("Content-Type", "unknown")
                if b"ncbi - www error blocked diagnostic" in raw.lower():
                    raise SourceBlocked("NCBI trả trang chặn HTML; đã ngừng PubMed, không tự vượt chặn.")
                if "text/html" in content_type.lower():
                    raise AgentError(f"PubMed trả HTML thay vì {output}; không phải kết quả rỗng.")
                return json.loads(raw) if output == "json" else ET.fromstring(raw)
            except SourceBlocked:
                raise
            except HTTPError as exc:
                with exc:
                    detail = exc.read(300).decode(errors="replace")
                reason = f"PubMed HTTP {exc.code}: {detail}"
                if exc.code not in {429, 500, 502, 503, 504}:
                    raise AgentError(reason) from exc
                try:
                    delay = max(1.0, float(exc.headers.get("Retry-After", "1")))
                except (TypeError, ValueError):
                    delay = 1.0
            except (OSError, URLError, ValueError, ET.ParseError) as exc:
                reason, delay = f"PubMed không trả {output} hợp lệ: {exc}", 1.0
            if attempt or delay >= deadline - time.monotonic():
                raise AgentError(reason)
            time.sleep(min(delay, 30))
        raise AgentError("PubMed chưa trả kết quả hợp lệ.")

    def search_pubmed(self, query, plan, timeout=30, limit=5):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Số kết quả PubMed từ 1 đến 100.")
        parameters = {"db": "pubmed", "term": query, "retmax": limit, "retmode": "json", "sort": "relevance"}
        if plan.get("start_year") is not None or plan.get("end_year") is not None:
            parameters.update({"datetype": "pdat", "mindate": f"{plan.get('start_year') or 1800}/01/01"})
            end_year = plan.get("end_year") or datetime.now(timezone.utc).year
            parameters["maxdate"] = min(f"{end_year}/12/31", datetime.now(timezone.utc).strftime("%Y/%m/%d"))
        key = "search:" + json.dumps(parameters, sort_keys=True)
        data = self.store.cache_get(key)
        mode = "cache" if data is not None else "live"
        if data is None:
            data = self._request("esearch.fcgi", parameters, "json", timeout)
            if not isinstance(data, dict) or not isinstance(data.get("esearchresult"), dict):
                raise AgentError("ESearch thiếu cấu trúc kết quả hợp lệ.")
            result = data["esearchresult"]
            if data.get("error") or result.get("ERROR") or result.get("errorlist"):
                raise AgentError(f"ESearch báo lỗi truy vấn: {data.get('error') or result.get('ERROR') or result.get('errorlist')}")
            ids, count = result.get("idlist"), result.get("count")
            if not isinstance(ids, list) or not all(isinstance(p, str) and p.isascii() and p.isdecimal() for p in ids) or not str(count).isdecimal():
                raise AgentError("ESearch thiếu PMID/count hợp lệ.")
            data = {**data, "fetched_at": utc_now()}
            self.store.cache_put(key, data)
        result = data["esearchresult"]
        candidates = []
        for pmid in result["idlist"]:
            cached = self.store.get_document("pubmed:" + pmid) or {}
            candidates.append({"id": "pubmed:" + pmid, "pmid": pmid, "title": cached.get("title"), "year": cached.get("year"), "origin": "pubmed"})
        return {"candidates": candidates, "total_matches": int(result["count"]), "query": query, "query_translation": result.get("querytranslation"), "warnings": result.get("warninglist", {}), "fetched_at": data["fetched_at"], "source_mode": mode}

    def search_local(self, query):
        return {"candidates": self.store.search_documents(query), "query": query, "source_mode": "local_corpus", "searched_at": utc_now()}

    def read_source(self, source_id, offline=False, timeout=30):
        cached = self.store.get_document(source_id)
        if cached is not None:
            fresh = False
            try:
                fresh = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"])).total_seconds() < 86400
            except (KeyError, TypeError, ValueError):
                pass
            if cached.get("origin") != "pubmed" or offline or fresh:
                return {**cached, "read_mode": "local_snapshot" if offline else "cache"}
        if offline or not source_id.startswith("pubmed:"):
            raise AgentError("Nguồn chưa có trong corpus local; không thực hiện request mạng ở chế độ này.")
        pmid = source_id.removeprefix("pubmed:")
        if not pmid.isascii() or not pmid.isdecimal():
            raise AgentError("PMID không hợp lệ.")
        root = self._request("efetch.fcgi", {"db": "pubmed", "id": pmid, "retmode": "xml"}, "xml", timeout)
        if root.tag != "PubmedArticleSet" or root.find(".//ERROR") is not None:
            raise AgentError("EFetch không trả bộ bản ghi PubMed hợp lệ.")
        for node in root:
            document = node.find("MedlineCitation") if node.tag == "PubmedArticle" else node.find("BookDocument")
            if document is None or text_of(document.find("PMID")) != pmid:
                continue
            article = document.find("Article") if node.tag == "PubmedArticle" else document
            if article is None:
                continue
            title = text_of(article.find("ArticleTitle")) or text_of(article.find("Book/BookTitle"))
            sections = []
            for section in article.findall("Abstract/AbstractText"):
                content = text_of(section)
                if content:
                    sections.append((section.get("Label") + ": " if section.get("Label") else "") + content)
            ids_path = "PubmedData" if node.tag == "PubmedArticle" else "PubmedBookData"
            year_path = "Journal/JournalIssue/PubDate/Year" if node.tag == "PubmedArticle" else "Book/PubDate/Year"
            record = {"pmid": pmid, "title": title, "publication_year": text_of(article.find(year_path)), "abstract": "\n".join(sections), "doi": text_of(node.find(f"{ids_path}/ArticleIdList/ArticleId[@IdType='doi']")) or None}
            doc = normalize_document(record, utc_now())
            self.store.put_document(doc)
            return {**doc, "read_mode": "live"}
        raise AgentError(f"EFetch không trả bản ghi PMID {pmid}; không tạo nguồn thay thế.")

    def ingest(self, path):
        path = Path(path)
        if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("Chọn một file txt/md/json/jsonl tối đa 10 MB cho lần nạp này.")
        raw = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() in {".txt", ".md"}:
            data = [{"title": path.name, "text": raw}]
            fetched_at = None
        elif path.suffix.lower() == ".jsonl":
            data = [json.loads(line) for line in raw.splitlines() if line.strip()]
            fetched_at = None
        elif path.suffix.lower() == ".json":
            payload = json.loads(raw)
            fetched_at = payload.get("fetched_at") if isinstance(payload, dict) else None
            data = payload.get("records", [payload]) if isinstance(payload, dict) else payload
        else:
            raise ValueError("Định dạng chưa hỗ trợ; dùng txt/md/json/jsonl.")
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise ValueError("Dữ liệu phải là danh sách bản ghi dạng object.")
        documents = []
        for row in data:
            if "pmid" in row:
                documents.append(normalize_document(row, fetched_at, imported=True))
                continue
            text = row.get("text", row.get("content", row.get("abstract", "")))
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Bản ghi local thiếu text/content/abstract dạng chuỗi.")
            text = unicodedata.normalize("NFC", text)
            identity = hashlib.sha256((str(path.resolve()) + text).encode()).hexdigest()[:16]
            year = row.get("year", row.get("publication_year"))
            year = int(year) if str(year).isdecimal() and len(str(year)) == 4 else None
            for offset in range(0, len(text), 2600):
                content = text[max(0, offset - 200):offset + 2600]
                documents.append({"id": f"local:{identity}:{offset}", "document_id": f"local:{identity}", "title": str(row.get("title") or path.name), "content": content, "year": year, "doi": row.get("doi") if isinstance(row.get("doi"), str) else None, "url": row.get("url") if isinstance(row.get("url"), str) and row["url"].startswith(("http://", "https://")) else None, "origin": "local", "path": str(path.resolve()), "content_scope": "local_document_chunk", "fetched_at": row.get("fetched_at") or fetched_at, "provenance": "user_local_document"})
        self.store.import_documents(path.resolve(), documents)
        return len(documents)
