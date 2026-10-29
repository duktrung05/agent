import re
import unicodedata
from html import escape


STUDY_FIELDS = {
    "population": "Quần thể", "study_design": "Thiết kế",
    "intervention_or_exposure": "Can thiệp/phơi nhiễm", "comparator": "Đối chứng",
    "outcome": "Kết cục/thước đo", "results": "Kết quả/số liệu", "limitations": "Giới hạn được nguồn nêu",
}


def extract_study_fields(raw, spans):
    """Chỉ chấp nhận ID đoạn có thật; không dùng văn bản model tự điền vào bảng."""
    result = {}
    for field in STUDY_FIELDS:
        ids = raw.get(field, []) if isinstance(raw, dict) else []
        if not isinstance(ids, list):
            ids = []
        ids = [sid for sid in ids[:1] if isinstance(sid, str) and sid in spans]
        result[field] = [{"span_id": sid, "quote": spans[sid]} for sid in ids]
    return result


def normalized(text):
    return " ".join(unicodedata.normalize("NFC", text).split())


def source_spans(content, width=360):
    """Đánh số các đoạn nguyên văn; model chọn ID, không phải tự chép lại nguồn."""
    pieces = []
    for sentence in re.split(r"(?<=[.!?])\s+", normalized(content)):
        while len(sentence) > width:
            cut = sentence.rfind(" ", 0, width + 1)
            cut = cut if cut > 0 else width
            pieces.append(sentence[:cut])
            sentence = sentence[cut:].strip()
        if sentence:
            pieces.append(sentence)
    return {f"s{i}": piece for i, piece in enumerate(pieces, 1)}


def evidence_ledger(state):
    return {e["id"]: {**e, "source_id": source_id} for source_id, source in state["documents"].items() if source.get("eligible") for e in source.get("assessment", {}).get("evidence", [])}


def original_source(state, source_id):
    # Group by shared identifiers, including DOI/PMID across different imports.
    def keys(sid, doc):
        values = {doc.get("document_id") or sid}
        if doc.get("pmid"):
            values.add("pubmed:" + str(doc["pmid"]))
        if doc.get("doi"):
            values.add("doi:" + re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doc["doi"].strip(), flags=re.I).casefold())
        return values

    identities = keys(source_id, state["documents"][source_id])
    while True:
        previous = set(identities)
        for sid, doc in state["documents"].items():
            other = keys(sid, doc)
            if identities & other:
                identities.update(other)
        if identities == previous:
            return min(identities)


def readiness(state):
    plan = state.get("plan") or {}
    ledger = evidence_ledger(state)
    sources = {original_source(state, e["source_id"]) for e in ledger.values()}
    covered = {c for e in ledger.values() for c in e["criteria"]}
    required = set(range(1, len(plan.get("criteria", [])) + 1))
    missing = sorted(required - covered)
    return {"ready": bool(plan) and len(sources) >= plan.get("target_sources", 2) and not missing, "usable_sources": len(sources), "target_sources": plan.get("target_sources", 2), "missing_criteria": missing}


def validate_claims(payload, state):
    ledger = evidence_ledger(state)
    accepted, errors = [], []
    claims = payload.get("claims") if isinstance(payload, dict) else None
    if not isinstance(claims, list):
        return [], ["Report thiếu danh sách claims."]
    for claim in claims[:4]:
        if not isinstance(claim, dict):
            errors.append("Claim không phải object.")
            continue
        text, ids, section = claim.get("text"), claim.get("evidence_ids"), claim.get("section")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 700 or not isinstance(ids, list) or not ids or not all(isinstance(e, str) and e in ledger for e in ids):
            errors.append("Claim thiếu nội dung hoặc tham chiếu chứng cứ hợp lệ.")
            continue
        if section not in {"summary", "agreement", "difference"}:
            errors.append("Loại claim không hợp lệ.")
            continue
        source_ids = {original_source(state, ledger[e]["source_id"]) for e in ids}
        known_pmids = {state["documents"][ledger[e]["source_id"]].get("pmid") for e in ids}
        if section in {"agreement", "difference"} and len(source_ids) < 2:
            errors.append("So sánh cần ít nhất hai nguồn khác nhau.")
            continue
        mentioned = set(re.findall(r"\bPMID\s*[:#]?\s*(\d+)|pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", text, re.I))
        mentioned_ids = {a or b for a, b in mentioned}
        if not mentioned_ids.issubset(known_pmids):
            errors.append("Claim nêu PMID chưa được đọc/chấp nhận.")
            continue
        # The bibliography/URLs are rendered from source records, never model text.
        if re.search(r"https?://|\b10\.\d{4,9}/\S+", text, re.I):
            errors.append("Claim chứa URL/DOI tự điền; tham chiếu phải được dựng từ bản ghi nguồn.")
            continue
        accepted.append({"text": text.strip(), "section": section, "evidence_ids": list(dict.fromkeys(ids))})
    return accepted, errors


def clean_cell(value):
    return escape(str(value if value is not None and value != "" else "chưa có")).replace("|", "\\|").replace("\n", " ")


def render_report(state):
    ledger = evidence_ledger(state)
    source_ids = sorted({item["source_id"] for item in ledger.values()})
    numbers = {sid: i + 1 for i, sid in enumerate(source_ids)}
    plan = state.get("plan") or {}
    ready = readiness(state)
    lines = ["# Báo cáo nghiên cứu local", "", f"**Mục tiêu:** {state['goal']}", "", f"**Run:** `{state['id']}` · **Trạng thái:** `{state['status']}`", f"**Mốc tra cứu:** {state['created_at']} · **Chế độ:** {'offline: dữ liệu đã lưu' if state['config']['offline'] else 'online/cache, xem nguồn bên dưới'}", "", "Bản nghiên cứu thử nghiệm từ phần tài liệu đã đọc; chưa được chuyên gia kiểm định nội dung y khoa.", ""]
    review = state.get("review", {})
    lines += [f"**Review:** `{review.get('status', 'pending')}` · người/nguồn review: `{clean_cell(review.get('reviewer'))}` · loại: `{review.get('kind', 'chưa có')}` — không phải chứng nhận y khoa.", ""]
    if state.get("review", {}).get("note"):
        lines += ["**Ghi chú review:** " + state["review"]["note"], ""]
    if plan:
        lines += ["## Phạm vi", "", f"- Chủ đề: {plan['topic']}", f"- Năm: {plan.get('start_year') or 'không giới hạn đầu'} – {plan.get('end_year') or 'đến mốc tra cứu'}", f"- Nguồn dùng được: {ready['usable_sources']}/{ready['target_sources']}"]
        lines += [f"- Tiêu chí {i}: {criterion}" for i, criterion in enumerate(plan["criteria"], 1)]
        if ready["missing_criteria"]:
            lines.append(f"- Tiêu chí chưa đủ chứng cứ: {ready['missing_criteria']}")
        lines += [""]
    spec = state.get("question_spec", {})
    if spec:
        lines += [f"- Khoảng năm người dùng nêu: {clean_cell(spec.get('requested_years'))}",
                  f"- Thời điểm chốt phạm vi: {clean_cell(spec.get('search_as_of'))}", ""]
    lines += ["## Nhật ký tìm kiếm", "", "| Tool | Truy vấn | Khoảng năm | Ứng viên | Nguồn dữ liệu | Thời điểm |", "|---|---|---|---|---|---|"]
    for search in state.get("search_history", []):
        lines.append("| " + " | ".join(clean_cell(search.get(k)) for k in ("action", "query", "scope", "count", "source_mode", "at")) + " |")
        if search.get("query_translation"):
            lines += ["", "PubMed query translation: " + clean_cell(search["query_translation"]), ""]
    lines += ["", "## Bảng nghiên cứu", "", "Các ô là đoạn nguyên văn được chọn từ phần đã đọc; chưa được đánh giá ngữ nghĩa độc lập.", "",
              "| Nguồn | Năm | " + " | ".join(STUDY_FIELDS.values()) + " | Phần đã đọc |",
              "|---|---|" + "---|" * (len(STUDY_FIELDS) + 1)]
    groups = {}
    for sid in source_ids:
        groups.setdefault(original_source(state, sid), []).append(sid)
    for group in groups.values():
        source = state["documents"][group[0]]
        cells = [" ".join(f"[{numbers[sid]}](#source-{numbers[sid]})" for sid in group), clean_cell(source.get("year"))]
        for field in STUDY_FIELDS:
            entries = []
            for sid in group:
                assessment = state["documents"][sid].get("assessment", {})
                items = assessment.get("study_fields", {}).get(field, [])
                if field == "results" and not items:
                    items = assessment.get("evidence", [])
                for item in items:
                    entries.append(clean_cell(item["quote"]) + f" ({numbers[sid]}/{clean_cell(item.get('span_id'))})")
            cells.append("; ".join(dict.fromkeys(entries)) or "chưa có trong phần đã đọc")
        cells.append(clean_cell(source.get("content_scope")))
        lines.append("| " + " | ".join(cells) + " |")
    lines += [""]
    lines += ["## Tổng hợp", ""]
    if state.get("claims"):
        for claim in state["claims"]:
            refs = sorted({numbers[ledger[e]["source_id"]] for e in claim["evidence_ids"]})
            links = " ".join(f"[{n}](#source-{n})" for n in refs)
            evidence_links = " ".join(f"[`{e}`](#evidence-{numbers[ledger[e]['source_id']]}-{e.rsplit('#', 1)[-1]})" for e in claim["evidence_ids"])
            lines.append("- " + escape(claim["text"]) + " " + links + " · Chứng cứ: " + evidence_links)
    elif ledger:
        lines.append("Chưa có bản tổng hợp đạt hợp đồng đầu ra. Các trích đoạn đã đối chiếu nằm bên dưới; không tự tạo kết luận thay thế.")
    else:
        lines.append("Chưa có bằng chứng dùng được. Đây không phải kết luận rằng không có nghiên cứu.")
    lines += ["", "## Nguồn và đoạn chứng cứ", ""]
    for sid in source_ids:
        source = state["documents"][sid]
        lines += [f'<a id="source-{numbers[sid]}"></a>', f"### [{numbers[sid]}] {escape(source.get('title') or sid)}", "", f"- ID: `{sid}` · năm: {source.get('year') or 'chưa có'} · DOI: {clean_cell(source.get('doi'))}", f"- Phần đã đọc: `{source.get('content_scope')}` · nguồn: `{source.get('read_mode') or source.get('provenance')}`", f"- Lấy dữ liệu lúc: {source.get('fetched_at') or 'không xác định từ file nhập'}"]
        if source.get("url"):
            lines.append("- URL: " + clean_cell(source["url"]))
        lines += [""]
        # Trích ngắn để người đọc kiểm tra, không tái xuất toàn bộ abstract.
        for evidence in source["assessment"]["evidence"][:2]:
            lines += [f'<a id="evidence-{numbers[sid]}-{evidence["id"].rsplit("#", 1)[-1]}"></a>',
                      f"**`{evidence['id']}`** · đoạn `{evidence.get('span_id', 'chưa có')}`", "", "> " + escape(evidence["quote"]), ""]
    lines += ["## Nguồn bị loại hoặc chưa dùng được", "", "| ID | Tiêu đề | Lý do |", "|---|---|---|"]
    for sid, source in state["documents"].items():
        if not source.get("eligible"):
            lines.append(f"| {clean_cell(sid)} | {clean_cell(source.get('title'))} | {clean_cell(source.get('assessment', {}).get('reason'))} |")
    lines += ["", "## Giới hạn và vận hành", "", "- Kiểm tra trích đoạn là đối chiếu văn bản; chưa chứng minh sự đúng đắn khoa học hay suy luận nhân quả.", "- Kết quả không phải systematic review hoặc chỉ định điều trị.", "- Model đánh giá độ liên quan và tiêu chí có thể sai; cần người đọc kiểm tra.", f"- Bước: {state['steps']} · tool: {state['tool_calls']} · model: {state['model_calls']} · giây chạy chủ động: {state['elapsed_seconds']:.2f}", f"- Lý do dừng: {state.get('stop_reason') or 'chưa kết thúc'}"]
    for observation in state.get("errors", []):
        lines.append("- Lỗi ghi nhận: " + str(observation))
    if state.get("report_errors"):
        lines.append("- Một số claim bị loại: " + "; ".join(state["report_errors"]))
    return "\n".join(lines) + "\n"
