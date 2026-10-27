"""Bài 4: PMID -> EFetch XML -> tiêu đề, DOI và abstract thực sự truy xuất được."""

import argparse
from datetime import datetime, timezone
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


def element_text(element: ET.Element | None) -> str | None:
    """Giữ cả chữ bên trong thẻ con, ví dụ <i>, <sup>, <sub>."""
    if element is None:
        return None
    return " ".join("".join(element.itertext()).split()) or None


def parse_records(xml_data: bytes, requested_pmids: list[str]) -> dict:
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise RuntimeError(f"EFetch trả XML không hợp lệ: {exc}") from exc
    if root.tag != "PubmedArticleSet" or root.find(".//ERROR") is not None:
        raise RuntimeError("EFetch báo lỗi hoặc response không phải PubmedArticleSet.")

    by_pmid = {}
    for record in root:
        if record.tag == "PubmedArticle":
            document = record.find("MedlineCitation")
            article = document.find("Article") if document is not None else None
            ids_path = "PubmedData/ArticleIdList"
            pubdate_path = "Journal/JournalIssue/PubDate/Year"
        elif record.tag == "PubmedBookArticle":
            document = record.find("BookDocument")
            article = document
            ids_path = "PubmedBookData/ArticleIdList"
            pubdate_path = "Book/PubDate/Year"
        else:
            raise RuntimeError(f"Loại bản ghi chưa được parser hỗ trợ: {record.tag}")
        if document is None or article is None:
            raise RuntimeError("Bản ghi thiếu cấu trúc tài liệu bắt buộc.")
        # Lấy PMID của bản ghi, không lấy PMID nằm trong danh mục tài liệu tham khảo.
        pmid = element_text(document.find("PMID"))
        if pmid not in requested_pmids or pmid in by_pmid:
            raise RuntimeError("EFetch trả PMID ngoài yêu cầu, bị thiếu hoặc bị trùng.")
        title = element_text(article.find("ArticleTitle"))
        if title is None and record.tag == "PubmedBookArticle":
            title = element_text(article.find("Book/BookTitle"))

        paragraphs = []
        for section in article.findall("Abstract/AbstractText"):
            text = element_text(section)
            if text:
                label = section.get("Label")
                paragraphs.append(f"{label}: {text}" if label else text)
        abstract = "\n".join(paragraphs) or None
        by_pmid[pmid] = {
            "pmid": pmid,
            "title": title,
            "publication_year": element_text(article.find(pubdate_path)),
            "doi": element_text(record.find(f"{ids_path}/ArticleId[@IdType='doi']")),
            "abstract": abstract,
            "content_scope": "metadata_and_abstract" if abstract else "metadata_only",
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }
    return {
        "requested_pmids": requested_pmids,
        "returned": len(by_pmid),
        "records": [by_pmid[p] for p in requested_pmids if p in by_pmid],
        "missing_pmids": [p for p in requested_pmids if p not in by_pmid],
    }


def fetch_pubmed_records(pmids: list[str]) -> dict:
    """Lấy tối đa 3 bản ghi trong một request; không gọi LLM hay lấy full-text."""
    if not isinstance(pmids, list) or not 1 <= len(pmids) <= 3:
        raise ValueError("pmids phải là danh sách từ 1 đến 3 mã.")
    if not all(isinstance(p, str) and p.isascii() and p.isdecimal() and int(p) > 0 for p in pmids):
        raise ValueError("Mỗi PMID phải là chuỗi số nguyên dương dùng chữ số 0–9.")
    pmids = list(dict.fromkeys(pmids))
    params = urlencode({
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": "medical_agent_learning",
    })
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + params
    request = Request(url, headers={"User-Agent": "medical-agent-learning/0.1"})
    try:
        with urlopen(request, timeout=30) as response:
            xml_data = response.read()
            status = response.status
            content_type = response.headers.get("Content-Type", "không rõ")
    except HTTPError as exc:
        with exc:
            detail = exc.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(f"PubMed HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Không lấy được bản ghi PubMed: {exc}") from exc
    if b"NCBI - WWW Error Blocked Diagnostic" in xml_data:
        raise RuntimeError(
            f"NCBI trả trang chặn HTML (HTTP {status}, Content-Type: {content_type}), "
            "không phải XML bài báo. Chưa xác định lý do bị chặn; đã dừng request, "
            "không kết luận thiếu bài báo hay abstract."
        )
    if "text/html" in content_type.lower():
        raise RuntimeError(f"EFetch trả HTML thay vì XML (HTTP {status}, Content-Type: {content_type}).")
    result = parse_records(xml_data, pmids)
    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmids", nargs="+", required=True, help="Từ 1 đến 3 PMID đã tìm được.")
    args = parser.parse_args()
    try:
        print(json.dumps(fetch_pubmed_records(args.pmids), ensure_ascii=False, indent=2))
    except (ValueError, RuntimeError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        sys.exit(1)
