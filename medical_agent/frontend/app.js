"use strict";

const $ = (id) => document.getElementById(id);
const examples = {
    sleep: "Tóm tắt 1 bài báo về giấc ngủ và Alzheimer; chỉ cần nêu mối liên hệ được tác giả mô tả.",
    screening: "So sánh bằng chứng về hiệu quả sàng lọc ung thư phổi bằng CT liều thấp ở nhóm có nguy cơ cao.",
    copd: "Các nghiên cứu từ 2020–2026 cho thấy chương trình phục hồi chức năng hô hấp ảnh hưởng thế nào đến chất lượng cuộc sống của người bệnh COPD?",
};
const statuses = {
    planning: "Đang lập kế hoạch", running: "Đang tra cứu", completed: "Đã tổng hợp",
    partial: "Kết quả một phần", waiting_for_user: "Cần bổ sung", paused_source: "Nguồn gián đoạn",
    interrupted: "Đã dừng", failed: "Gặp lỗi", out_of_scope: "Ngoài phạm vi",
};
const studyFields = {
    population: "Quần thể", study_design: "Thiết kế", intervention_or_exposure: "Can thiệp / phơi nhiễm",
    comparator: "Đối chứng", outcome: "Kết cục / thước đo", results: "Kết quả", limitations: "Giới hạn được nguồn nêu",
};
const app = { token: "", runs: [], runCount: 0, activeJob: null, selectedRun: null, tab: "overview", health: null,
    poll: null, pollErrors: 0, libraryRequest: 0, evidenceTargets: {}, toastTimer: null };

function node(tag, className = "", text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
}

function icon(name, className = "") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.setAttribute("aria-hidden", "true");
    if (className) svg.setAttribute("class", className);
    svg.append(use);
    return svg;
}

function button(text, className, onClick, iconName) {
    const result = node("button", className, text);
    result.type = "button";
    if (iconName) result.append(icon(iconName));
    result.addEventListener("click", onClick);
    return result;
}

function badge(status) {
    return node("span", `status-badge ${status}`, statuses[status] || status);
}

function date(value, withTime = false) {
    const parsed = new Date(value);
    if (!value || Number.isNaN(parsed.getTime())) return "Chưa có thời điểm";
    return new Intl.DateTimeFormat("vi-VN", {
        timeZone: "Asia/Ho_Chi_Minh", day: "2-digit", month: "2-digit", year: "numeric",
        ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
    }).format(parsed);
}

function safeLink(url, text, className = "document-link") {
    try {
        const parsed = new URL(url);
        if (!["http:", "https:"].includes(parsed.protocol)) return null;
        const link = node("a", className, text);
        link.href = parsed.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.append(icon("link"));
        return link;
    } catch { return null; }
}

async function api(path, payload) {
    const response = await fetch(path, {
        headers: payload === undefined ? {} : { "Content-Type": "application/json", "X-App-Token": app.token },
        ...(payload === undefined ? {} : { method: "POST", body: JSON.stringify(payload) }),
    });
    const data = await response.json();
    if (!response.ok) {
        const error = new Error(data.error || "Chưa thực hiện được thao tác. Hãy thử lại.");
        error.status = response.status;
        throw error;
    }
    return data;
}

function showError(id, message) {
    const target = $(id);
    target.textContent = message || "";
    target.hidden = !message;
}

function toast(message) {
    clearTimeout(app.toastTimer);
    $("toast").textContent = message;
    $("toast").hidden = false;
    app.toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4500);
}

function updateButtons() {
    $("submit-research").disabled = Boolean(app.activeJob) || app.health !== true || !app.token;
    $("submit-research").title = app.activeJob ? "Đang có một phiên nghiên cứu" : app.health === false ? "Bật Ollama và model trước khi tra cứu" : "Bắt đầu nghiên cứu";
    $("active-session").hidden = !app.activeJob;
}

async function refreshData() {
    const data = await api("/api/bootstrap");
    app.token = data.csrf_token;
    app.runs = data.runs;
    app.runCount = data.runs_count;
    app.activeJob = data.active_job;
    $("nav-doc-count").textContent = data.documents_count;
    $("sidebar-library").textContent = `${data.documents_count} tài liệu có nội dung`;
    renderRecent();
    renderHistory();
    updateButtons();
    return data;
}

async function checkHealth() {
    const target = $("health-status");
    try {
        const data = await api("/api/health");
        app.health = data.available;
        target.replaceChildren(node("span", `status-dot ${data.available ? "" : "offline"}`), node("span", "", data.available ? "Model cục bộ sẵn sàng" : "Chưa kết nối model"));
        target.title = data.message;
    } catch {
        app.health = false;
        target.replaceChildren(node("span", "status-dot offline"), node("span", "", "Mất kết nối backend"));
    }
    updateButtons();
}

function emptyState(title, description, iconName = "book") {
    const box = node("div", "empty-state");
    box.append(icon(iconName), node("strong", "", title), node("p", "", description));
    return box;
}

function runRow(run) {
    const row = button("", "run-row", () => { location.hash = `run=${run.id}`; });
    row.setAttribute("aria-label", `Xem phiên: ${run.goal}`);
    const copy = node("div", "run-row-copy");
    const meta = node("div", "run-row-meta");
    meta.append(node("span", "", date(run.created_at, true)), badge(run.status));
    copy.append(node("div", "run-row-title", run.goal), meta);
    const rowIcon = node("span", "run-row-icon");
    rowIcon.append(icon("file"));
    row.append(rowIcon, copy, icon("arrow", "row-arrow"));
    return row;
}

function renderRecent() {
    const list = $("recent-runs");
    list.replaceChildren(...app.runs.slice(0, 3).map(runRow));
    if (!app.runs.length) list.append(node("p", "muted", "Phiên nghiên cứu đầu tiên của bạn sẽ xuất hiện ở đây."));
}

function renderHistory() {
    const filter = $("history-filter").value;
    const visible = app.runs.filter(run => filter === "all" || run.status === filter);
    $("history-meta").textContent = `${app.runCount} phiên đã lưu · hiển thị tối đa 30 phiên gần nhất`;
    $("history-list").replaceChildren(...visible.map(runRow));
    if (!visible.length) $("history-list").replaceChildren(emptyState("Chưa có phiên phù hợp", "Bắt đầu một tra cứu mới hoặc chọn trạng thái khác.", "clock"));
}

async function renderLibrary() {
    const serial = ++app.libraryRequest;
    $("library-meta").textContent = "Đang đọc thư viện…";
    try {
        const data = await api(`/api/documents?q=${encodeURIComponent($("library-query").value)}`);
        if (serial !== app.libraryRequest) return;
        $("library-meta").textContent = `${data.documents.length} bản ghi hiển thị · ${data.total} tài liệu / đoạn có nội dung trong thư viện`;
        const cards = data.documents.map(doc => {
            const card = node("article", "document-card");
            const top = node("div", "document-top");
            const mark = node("span", "topic-icon mint");
            mark.append(icon("file"));
            const title = node("div");
            const meta = node("div", "document-meta");
            meta.append(node("span", "", doc.year || "Chưa xác định năm"), node("span", "", doc.pmid ? `PMID ${doc.pmid}` : "Tài liệu cục bộ"), node("span", "", `Lấy nguồn: ${date(doc.fetched_at)}`));
            title.append(node("h2", "document-title", doc.title || "Tài liệu chưa có tiêu đề"), meta);
            top.append(mark, title);
            const details = node("details", "document-content");
            details.append(node("summary", "", "Đọc phần nội dung đã lưu"), node("p", "", doc.content || "Bản ghi chưa có abstract hoặc nội dung."));
            card.append(top, details);
            const link = safeLink(doc.url, "Mở nguồn gốc");
            if (link) card.append(link);
            return card;
        });
        $("library-list").replaceChildren(...cards);
        if (!cards.length) $("library-list").append(emptyState("Chưa tìm thấy tài liệu phù hợp", "Thử từ khóa khác hoặc thêm tài liệu vào thư viện."));
    } catch (error) {
        $("library-meta").textContent = "";
        $("library-list").replaceChildren(emptyState("Chưa tải được thư viện", error.message));
    }
}

function page(name) {
    for (const id of ["search", "library", "history", "run"]) $(`${id}-view`).hidden = id !== name;
    document.querySelectorAll("[data-nav]").forEach(link => {
        if (link.dataset.nav === name) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
    });
    $("breadcrumb-title").textContent = { search: "Tra cứu y văn", library: "Thư viện tài liệu", history: "Lịch sử tra cứu", run: "Phiên nghiên cứu" }[name];
}

async function route() {
    const hash = location.hash.slice(1) || "search";
    if (hash === "library") { page("library"); await renderLibrary(); }
    else if (hash === "history") { page("history"); renderHistory(); }
    else if (hash.startsWith("run=")) {
        page("run");
        const id = hash.slice(4);
        if (!/^[a-f0-9]{12}$/.test(id)) { showRunError("ID phiên không hợp lệ."); return; }
        try {
            const data = await api(`/api/runs/${id}`);
            if (location.hash !== `#run=${id}`) return;
            app.selectedRun = data.run;
            renderRun(data.run, data.events);
        } catch (error) { showRunError(error.message); }
    } else if (hash === "working" && app.activeJob) {
        page("run");
        $("run-header").replaceChildren(node("h1", "run-title", "Đang khởi tạo phiên nghiên cứu…"));
        $("run-status").replaceChildren();
        $("run-body").replaceChildren();
    } else { page("search"); }
}

function showRunError(message) {
    $("run-header").replaceChildren();
    $("run-status").replaceChildren();
    $("run-body").replaceChildren(emptyState("Chưa mở được phiên nghiên cứu", message, "file"));
}

function runIsActive(run) {
    return app.activeJob && app.activeJob.run_id === run.id;
}

function progress(run, events) {
    let step = run.plan ? 1 : 0;
    if (run.sources.length) step = 2;
    if (events.some(e => e.kind === "model_started" && e.purpose === "report") || ["completed", "partial"].includes(run.status)) step = 3;
    const box = node("div", "research-progress");
    ["Hiểu câu hỏi", "Tìm tài liệu", "Đọc & đối chiếu", "Tổng hợp"].forEach((label, index) => {
        const cell = node("div", `progress-step ${index < step ? "done" : index === step ? "current" : ""}`);
        const number = node("span", "step-number", index < step ? "✓" : index + 1);
        cell.append(number, node("span", "", label));
        box.append(cell);
    });
    return box;
}

function eventTitle(event) {
    const titles = { run_created: "Đã tạo phiên nghiên cứu", plan_created: "Đã xác định kế hoạch và tiêu chí", model_completed: "Đã nhận kết quả từ model",
        tool_completed: "Đã hoàn tất tìm / đọc nguồn", source_assessed: "Đã đối chiếu một nguồn", waiting_for_user: "Cần bạn bổ sung thông tin", run_finished: "Đã lưu kết quả phiên",
        run_resumed: "Tiếp tục phiên đã lưu", run_interrupted: "Đã giữ checkpoint trước khi dừng", source_blocked: "Nguồn trực tuyến đang chặn truy cập",
        tool_error: "Nguồn dữ liệu gặp lỗi", policy_rejection: "Một hành động không hợp lệ đã bị chặn", run_error: "Phiên gặp lỗi xử lý", human_review: "Đã ghi nhận review", scope_rejected: "Yêu cầu nằm ngoài phạm vi nghiên cứu" };
    if (event.kind === "model_started") return { plan: "Đang phân tích câu hỏi", decide: "Đang chọn bước tiếp theo", assess_source: "Đang đọc và chọn đoạn chứng cứ", report: "Đang tổng hợp báo cáo" }[event.purpose] || "Đang xử lý với model";
    if (event.kind === "tool_started") return { search_local: "Đang tìm trong thư viện đã lưu", search_pubmed: "Đang tìm tài liệu trên PubMed", read_source: "Đang lấy nội dung nguồn" }[event.action] || "Đang truy cập nguồn";
    if (event.kind === "decision") return "Đã chọn hành động tiếp theo";
    return titles[event.kind] || "Đã cập nhật tiến trình";
}

function activity(events) {
    const panel = node("section", "run-panel");
    panel.append(node("h2", "", "Nhật ký hoạt động"));
    const list = node("ol", "activity-list");
    for (const event of events.slice().reverse()) {
        const item = node("li");
        const parsed = new Date(event.at);
        const time = Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleTimeString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit", second: "2-digit" });
        item.append(node("time", "", time), node("strong", "", eventTitle(event)), node("code", "", `#${event.seq}`));
        list.append(item);
    }
    if (!events.length) list.append(node("li", "muted", "Chưa có sự kiện được lưu."));
    panel.append(list);
    return panel;
}

function studyValue(source, field) {
    let items = source.assessment?.study_fields?.[field] || [];
    if (field === "results" && !items.length) items = source.assessment?.evidence || [];
    return items.map(item => item.quote).filter(Boolean).join(" ") || "Chưa có trong phần đã đọc";
}

function renderSources(run) {
    const container = node("div");
    app.evidenceTargets = {};
    run.sources.forEach((source, index) => {
        const panel = node("article", "run-panel");
        panel.append(node("h2", "", `[${index + 1}] ${source.title || source.id}`));
        const meta = node("div", "document-meta");
        meta.append(node("span", "", source.year || "Chưa có năm"), node("span", "", source.pmid ? `PMID ${source.pmid}` : source.id), node("span", "", source.eligible ? "Có chứng cứ được chọn" : "Chưa dùng được"));
        panel.append(meta, node("p", "source-scope-note", `Phần đã đọc: ${source.content_scope || "chưa rõ"} · Lấy nguồn: ${date(source.fetched_at, true)}`));
        const link = safeLink(source.url, "Đọc nguồn gốc");
        if (link) panel.append(link);
        if (!source.eligible) panel.append(node("p", "", source.assessment?.reason || "Chưa có chứng cứ phù hợp."));
        for (const [j, evidence] of (source.assessment?.evidence || []).entries()) {
            const proof = node("div", "source-proof");
            proof.id = `proof-${index}-${j}`;
            app.evidenceTargets[evidence.id] = proof.id;
            proof.append(node("div", "evidence-title", "Đoạn chứng cứ nguyên văn"), node("code", "", evidence.id), node("blockquote", "", evidence.quote));
            panel.append(proof);
        }
        const details = node("details", "document-content");
        details.append(node("summary", "", "Xem các trường nghiên cứu"));
        for (const [field, label] of Object.entries(studyFields)) details.append(node("p", "", `${label}: ${studyValue(source, field)}`));
        panel.append(details);
        container.append(panel);
    });
    if (!run.sources.length) container.append(emptyState("Chưa có nguồn đã đọc", "Xem tiến trình hoặc điều chỉnh câu hỏi theo dữ liệu hiện có.", "file"));
    return container;
}

function renderOverview(run) {
    const container = node("div");
    const panel = node("section", "run-panel");
    panel.append(node("h2", "", "Tổng hợp từ các nguồn đã đọc"), node("p", "source-scope-note", "Nội dung do model tạo, cần đối chiếu đoạn nguồn. Trích dẫn hợp lệ chưa xác nhận bản dịch hoặc diễn giải đúng."));
    for (const claim of run.claims) {
        const box = node("article", "claim");
        box.append(node("p", "", claim.text));
        for (const id of claim.evidence_ids) box.append(button(id, "evidence-tag", () => {
            selectRunTab("sources");
            const target = document.getElementById(app.evidenceTargets[id]);
            if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
        }));
        panel.append(box);
    }
    if (!run.claims.length) panel.append(node("p", "", "Chưa có nhận định tổng hợp đạt hợp đồng đầu ra. Không tự suy ra kết luận khi thiếu bằng chứng."));
    container.append(panel);
    if (run.sources.some(s => s.eligible)) {
        const tablePanel = node("section", "run-panel");
        tablePanel.append(node("h2", "", "Bảng đối chiếu nghiên cứu"), node("p", "source-scope-note", "Các ô là trích đoạn nguyên văn. Nhiều đoạn có thể thuộc cùng một tài liệu; số nguồn gốc được kiểm tra riêng."));
        const wrap = node("div", "study-table-wrap");
        const table = node("table", "study-table");
        const head = node("thead");
        const row = node("tr");
        for (const title of ["Nghiên cứu", "Quần thể", "Thiết kế", "Kết cục", "Kết quả"]) row.append(node("th", "", title));
        head.append(row);
        const body = node("tbody");
        for (const source of run.sources.filter(s => s.eligible)) {
            const tr = node("tr");
            tr.append(node("td", "", `${source.title || source.id} (${source.year || "chưa có năm"})`));
            for (const field of ["population", "study_design", "outcome", "results"]) tr.append(node("td", "", studyValue(source, field)));
            body.append(tr);
        }
        table.append(head, body); wrap.append(table); tablePanel.append(wrap); container.append(tablePanel);
    }
    if (run.plan) {
        const plan = node("section", "run-panel");
        plan.append(node("h2", "", "Phạm vi và tiêu chí"), node("p", "", `Khoảng năm: ${run.plan.start_year || "không giới hạn đầu"} – ${run.plan.end_year || "đến thời điểm tra cứu"}. Nguồn gốc dùng được: ${run.readiness.usable_sources}/${run.readiness.target_sources}.`));
        const criteria = node("ul", "criteria-list");
        run.plan.criteria.forEach((text, index) => criteria.append(node("li", "", `${text}${run.readiness.missing_criteria.includes(index + 1) ? " — chưa đủ chứng cứ" : ""}`)));
        plan.append(criteria); container.append(plan);
    }
    if (run.errors.length || run.report_errors.length) {
        const errors = node("section", "run-panel");
        errors.append(node("h2", "", "Lỗi và giới hạn được ghi nhận"));
        for (const message of [...run.errors, ...run.report_errors]) errors.append(node("p", "", message));
        container.append(errors);
    }
    return container;
}

function selectRunTab(name) {
    app.tab = name;
    document.querySelectorAll("[data-run-tab]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.runTab === name)));
    document.querySelectorAll("[data-tab-panel]").forEach(p => { p.hidden = p.dataset.tabPanel !== name; });
}

function renderRun(run, events) {
    const active = runIsActive(run);
    const header = node("div");
    const top = node("div", "run-header-top");
    top.append(node("div", "eyebrow", "PHIÊN NGHIÊN CỨU"), badge(run.status));
    const subtitle = node("div", "run-subtitle");
    subtitle.append(node("span", "", date(run.created_at, true)), node("span", "", run.config.offline ? "Dữ liệu đã lưu" : "PubMed / thư viện"), node("span", "", `Mã phiên ${run.id}`));
    header.append(top, node("h1", "run-title", run.goal), subtitle);
    if (!active) {
        const actions = node("div", "run-actions");
        const download = node("a", "secondary-button", "Tải báo cáo");
        download.href = `/api/runs/${run.id}/report`; download.download = `medisearch-${run.id}.md`; download.append(icon("down")); actions.append(download);
        if (["completed", "partial", "failed", "out_of_scope"].includes(run.status)) actions.append(button("Đối chiếu báo cáo", "secondary-button", () => { $("review-dialog").dataset.runId = run.id; $("review-dialog").showModal(); }, "check"));
        else if (run.status !== "waiting_for_user") actions.append(button("Tiếp tục phiên", "secondary-button", () => resumeRun(run.id, {}), "arrow"));
        header.append(actions);
    }
    $("run-header").replaceChildren(header);
    const banner = node("div", `run-banner ${["partial", "waiting_for_user", "paused_source", "interrupted"].includes(run.status) ? "warning" : ["failed", "out_of_scope"].includes(run.status) ? "error" : ""}`);
    if (active) {
        banner.append(node("span", "spinner"));
        const copy = node("div");
        copy.append(node("strong", "", app.activeJob.status === "stopping" ? "Đang dừng sau bước hiện tại…" : "Agent đang xử lý câu hỏi của bạn"), node("p", "", "Bạn có thể xem thư viện hoặc lịch sử trong khi phiên tiếp tục chạy."));
        const cancel = button(app.activeJob.status === "stopping" ? "Đang dừng…" : "Dừng phiên", "secondary-button", cancelActive);
        cancel.disabled = app.activeJob.status === "stopping";
        banner.append(copy, cancel);
    } else {
        const text = {
            completed: "Đã tạo bản tổng hợp. Hãy đọc và đối chiếu nội dung trước khi sử dụng.",
            partial: "Phiên chưa đáp ứng đủ tiêu chí. Báo cáo giữ phần đã có và chỉ rõ giới hạn.",
            waiting_for_user: "Cần bạn bổ sung thông tin để tiếp tục cùng phiên nghiên cứu.",
            interrupted: "Đã giữ checkpoint và ngân sách. Bạn có thể tiếp tục phiên này.",
            paused_source: "Nguồn trực tuyến tạm thời không truy cập được. Kết quả đã có được giữ lại.",
            failed: "Chưa hoàn tất được phiên. Xem lỗi bên dưới và kiểm tra kết nối model / dữ liệu.",
            out_of_scope: "Yêu cầu nằm ngoài phạm vi hỗ trợ tra cứu tài liệu của hệ thống.",
        }[run.status] || "Phiên chưa kết thúc. Có thể tiếp tục từ checkpoint đã lưu.";
        banner.append(icon("shield"), node("span", "", text));
    }
    $("run-status").replaceChildren(banner);
    const body = node("div");
    if (active) body.append(progress(run, events), activity(events));
    else {
        if (run.status === "waiting_for_user") {
            const panel = node("section", "run-panel");
            panel.append(node("h2", "", "Bổ sung phạm vi tra cứu"));
            const form = node("form", "clarification-form");
            const label = node("label", "", run.question || "Bạn muốn bổ sung phạm vi nào?"); label.htmlFor = "clarification-answer";
            const input = node("textarea"); input.id = "clarification-answer"; input.required = true; input.maxLength = 2000; input.rows = 2; input.placeholder = "Ví dụ: Dùng khoảng năm 2020–2026";
            const submit = node("button", "primary-button", "Tiếp tục nghiên cứu"); submit.type = "submit"; submit.append(icon("arrow"));
            form.append(label, input, submit);
            form.addEventListener("submit", async e => { e.preventDefault(); submit.disabled = true; try { await resumeRun(run.id, { answer: input.value }); } finally { submit.disabled = false; } });
            panel.append(form); body.append(panel);
        }
        const metrics = node("div", "run-metrics");
        metrics.append(node("span", "", `${run.sources.length} nguồn đã đọc`), node("span", "", `${run.model_calls} lượt model`), node("span", "", `${Number(run.elapsed_seconds).toFixed(1)} giây xử lý`));
        const reviewLabels = { pending: "Chờ đối chiếu", approved: "Đã ghi nhận đối chiếu", rejected: "Cần xem lại nội dung" };
        metrics.append(node("span", "", `Review: ${reviewLabels[run.review?.status] || "Chưa có"}`));
        body.append(metrics);
        const tabs = node("div", "run-tabs"); tabs.setAttribute("role", "tablist"); tabs.setAttribute("aria-label", "Nội dung phiên nghiên cứu");
        for (const [name, title] of [["overview", "Tổng quan"], ["sources", `Nguồn & chứng cứ (${run.sources.length})`], ["activity", "Hoạt động"]]) {
            const tab = button(title, "", () => selectRunTab(name));
            tab.dataset.runTab = name; tab.setAttribute("role", "tab"); tab.id = `tab-${name}`; tab.setAttribute("aria-controls", `panel-${name}`); tabs.append(tab);
        }
        tabs.addEventListener("keydown", event => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault();
            const buttons = [...tabs.querySelectorAll('[role="tab"]')];
            const index = buttons.indexOf(document.activeElement);
            const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
            buttons[next].focus(); selectRunTab(buttons[next].dataset.runTab);
        });
        body.append(tabs);
        for (const [name, panel] of [["overview", renderOverview(run)], ["sources", renderSources(run)], ["activity", activity(events)]]) {
            panel.dataset.tabPanel = name; panel.id = `panel-${name}`; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", `tab-${name}`); body.append(panel);
        }
        if (run.review?.note) {
            const review = node("section", "run-panel"); review.append(node("h2", "", "Ghi chú đối chiếu"), node("p", "", run.review.note), node("p", "source-scope-note", `Người / nguồn review: ${run.review.reviewer || "chưa có"} · Loại: ${run.review.kind || "chưa có"}`)); body.append(review);
        }
    }
    $("run-body").replaceChildren(body);
    if (!active) selectRunTab(app.tab);
}

async function startPoll(job) {
    clearTimeout(app.poll);
    app.activeJob = job;
    updateButtons();
    async function tick() {
        try {
            const data = await api(`/api/jobs/${job.id}`);
            app.pollErrors = 0;
            app.activeJob = data.job;
            const wasWorking = location.hash === "#working";
            if (data.run && wasWorking) {
                history.replaceState(null, "", `#run=${data.run.id}`);
                page("run");
            }
            const finished = !["queued", "running", "stopping"].includes(data.job.status);
            if (finished) app.activeJob = null;
            if (data.run && location.hash === `#run=${data.run.id}`) {
                app.selectedRun = data.run;
                renderRun(data.run, data.events);
            }
            updateButtons();
            if (finished) {
                await refreshData();
                if (!data.run && location.hash === "#working") showRunError(data.job.error || "Không khởi tạo được phiên nghiên cứu.");
                if (data.job.error) toast(`Phiên gặp lỗi: ${data.job.error}`);
                return;
            }
            app.poll = setTimeout(tick, 1200);
        } catch (error) {
            app.pollErrors++;
            if (app.pollErrors < 4) app.poll = setTimeout(tick, 2500);
            else showError("connection-error", "Chưa theo dõi được tiến trình. Hãy tải lại trang để kết nối với checkpoint đã lưu. " + error.message);
        }
    }
    await tick();
}

async function resumeRun(id, payload) {
    try {
        const data = await api(`/api/runs/${id}/resume`, payload);
        app.tab = "overview";
        location.hash = `run=${id}`;
        await startPoll(data.job);
    } catch (error) { toast(error.message); }
}

async function cancelActive() {
    if (!app.activeJob) return;
    try {
        const data = await api(`/api/jobs/${app.activeJob.id}/cancel`, {});
        app.activeJob = data.job;
        toast("Đã yêu cầu dừng. Agent sẽ giữ checkpoint sau bước hiện tại.");
        await route();
    } catch (error) { toast(error.message); }
}

$("research-form").addEventListener("submit", async e => {
    e.preventDefault();
    showError("question-error", "");
    if (!$("question").value.trim()) { showError("question-error", "Hãy nhập câu hỏi nghiên cứu."); return; }
    $("submit-research").disabled = true;
    try {
        const data = await api("/api/runs", { question: $("question").value,
            offline: document.querySelector('input[name="source"]:checked').value === "local",
            max_sources: Number($("max-sources").value), max_seconds: Number($("max-seconds").value) });
        app.tab = "overview"; app.activeJob = data.job;
        location.hash = "working";
        await route();
        await startPoll(data.job);
    } catch (error) { showError("question-error", error.message); updateButtons(); }
});

$("question").addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); if (!$("submit-research").disabled) $("research-form").requestSubmit(); }
});
document.querySelectorAll("[data-example]").forEach(card => card.addEventListener("click", () => {
    $("question").value = examples[card.dataset.example]; $("question").focus(); showError("question-error", "");
}));
document.querySelectorAll('input[name="source"]').forEach(input => input.addEventListener("change", () => {
    $("source-hint").replaceChildren(node("span", "tiny-dot"), node("span", "", input.value === "local" ? "Tra cứu trong thư viện đã nạp. Không cần kết nối PubMed." : "Kết nối PubMed khi tra cứu. Nguồn có thể tạm giới hạn truy cập."));
}));
$("toggle-options").addEventListener("click", () => {
    $("advanced-options").hidden = !$("advanced-options").hidden;
    $("toggle-options").setAttribute("aria-expanded", String(!$("advanced-options").hidden));
});
$("library-search").addEventListener("submit", e => { e.preventDefault(); renderLibrary(); });
$("history-filter").addEventListener("change", renderHistory);
$("active-session").addEventListener("click", () => { if (app.activeJob) location.hash = app.activeJob.run_id ? `run=${app.activeJob.run_id}` : "working"; });
document.querySelectorAll("[data-open]").forEach(b => b.addEventListener("click", () => $(b.dataset.open).showModal()));
document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", () => b.closest("dialog").close()));
document.querySelector(".skip-link").addEventListener("click", event => { event.preventDefault(); $("main-content").focus(); });
document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault(); location.hash = "search"; requestAnimationFrame(() => $("question").focus());
    }
});

$("upload-form").addEventListener("submit", async e => {
    e.preventDefault(); showError("upload-error", "");
    const file = $("document-file").files[0];
    if (!file) return;
    if (file.size > 10 * 1024 * 1024) { showError("upload-error", "Chọn file tối đa 10 MB."); return; }
    $("import-button").disabled = true;
    try {
        const data = await api("/api/documents", { name: file.name, content: await file.text() });
        $("upload-dialog").close(); $("upload-form").reset();
        toast(`Đã nhập ${data.imported} tài liệu / đoạn vào thư viện.`);
        await refreshData(); if (location.hash === "#library") await renderLibrary();
    } catch (error) { showError("upload-error", error.message); }
    finally { $("import-button").disabled = false; }
});

$("review-form").addEventListener("submit", async e => {
    e.preventDefault(); showError("review-error", "");
    const id = $("review-dialog").dataset.runId;
    const submit = $("review-form").querySelector('button[type="submit"]'); submit.disabled = true;
    try {
        const data = await api(`/api/runs/${id}/review`, { decision: $("review-decision").value, reviewer: $("reviewer").value, note: $("review-note").value });
        $("review-dialog").close(); $("review-form").reset();
        app.selectedRun = data.run; renderRun(data.run, data.events); await refreshData(); toast("Đã lưu ghi chú đối chiếu.");
    } catch (error) { showError("review-error", error.message); }
    finally { submit.disabled = false; }
});

window.addEventListener("hashchange", route);
async function init() {
    try {
        const data = await refreshData();
        await route();
        checkHealth();
        if (data.active_job) startPoll(data.active_job);
    } catch (error) {
        showError("connection-error", "Chưa kết nối được ứng dụng. Hãy kiểm tra server local và tải lại trang. " + error.message);
    }
}
init();
