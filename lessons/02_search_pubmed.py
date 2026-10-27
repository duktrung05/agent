"""Bài 2: tìm PMID trên PubMed bằng HTTP API, chưa gọi LLM."""

import argparse
from datetime import datetime, timezone
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def request_search_json(request: Request) -> object:
    """ESearch là thao tác đọc: thử lại tối đa một lần nếu lỗi tạm thời/JSON hỏng."""
    for attempt in range(2):
        retryable = True
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "không rõ")
            if b"NCBI - WWW Error Blocked Diagnostic" in raw:
                raise RuntimeError(
                    f"NCBI trả trang chặn HTML (HTTP {status}, Content-Type: {content_type}). "
                    "Chưa xác định lý do bị chặn; đã dừng, không tự thử lại trang chặn."
                )
            return json.loads(raw)
        except HTTPError as exc:
            with exc:
                detail = exc.read(300).decode("utf-8", errors="replace")
            reason = f"PubMed HTTP {exc.code}: {detail}"
            retryable = exc.code in {429, 500, 502, 503, 504}
        except (URLError, TimeoutError) as exc:
            reason = f"Không truy cập được PubMed: {exc}"
        except (json.JSONDecodeError, UnicodeDecodeError):
            preview = " ".join(raw[:160].decode("utf-8", errors="replace").split())
            reason = (
                f"PubMed trả nội dung không phải JSON hợp lệ "
                f"(HTTP {status}, Content-Type: {content_type}, "
                f"{len(raw)} bytes). Đầu response: {preview!r}"
            )
        if not retryable or attempt == 1:
            raise RuntimeError(reason)
        print(f"[PubMed] {reason} Thử lại một lần sau 1 giây.", file=sys.stderr, flush=True)
        time.sleep(1)
    raise RuntimeError("Chưa nhận được response PubMed hợp lệ.")


def search_pubmed(query: str, limit: int = 3) -> dict:
    """Tìm bản ghi theo query, trả PMID và thông tin của lượt tìm."""
    if not isinstance(query, str) or not query.strip() or type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("Query không được rỗng; limit phải từ 1 đến 10.")

    # urlencode giữ AND, dấu cách và ký tự đặc biệt trong giá trị query.
    params = urlencode({
        "db": "pubmed",
        "term": query,
        "retmax": limit,
        "retmode": "json",
        "sort": "relevance",
        "tool": "medical_agent_learning",
    })
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + params
    request = Request(url, headers={
        "User-Agent": "medical-agent-learning/0.1",
        "Accept": "application/json",
    })
    data = request_search_json(request)

    if not isinstance(data, dict) or data.get("error") or data.get("ERROR"):
        raise RuntimeError(f"Response PubMed không hợp lệ hoặc báo lỗi: {data}")
    result = data.get("esearchresult")
    if not isinstance(result, dict) or result.get("ERROR"):
        raise RuntimeError(f"Thiếu kết quả ESearch hợp lệ: {result}")
    pmids = result.get("idlist")
    count = result.get("count")
    if not isinstance(pmids, list) or not all(isinstance(p, str) and p.isdecimal() for p in pmids):
        raise RuntimeError("Response thiếu danh sách PMID hợp lệ.")
    if not isinstance(count, str) or not count.isdecimal():
        raise RuntimeError("Response thiếu tổng số kết quả hợp lệ.")

    return {
        "query": query,
        "total_matches": int(count),
        "returned": len(pmids),
        "pmids": pmids,
        "query_translation": result.get("querytranslation"),
        "query_errors": result.get("errorlist", {}),
        "query_warnings": result.get("warninglist", {}),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    try:
        print(json.dumps(search_pubmed(args.query, args.limit), ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        sys.exit(1)
