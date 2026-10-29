# Pipeline học theo 15 phần PDF

Nguồn: [Agent-Vin.pdf](../../Agent-Vin.pdf). Mỗi Day là một chương, không phải hạn một ngày.
Các sơ đồ là thiết kế để học; chưa phải các tính năng đã triển khai.

**Ứng dụng hiện tại:** `python3 -m medical_agent`. Xem [sơ đồ bản local v1 và cách đọc code](system.md).

## 1. Bản đồ học

```mermaid
flowchart TD
    D1["01 · LLM / API / token"] --> D2["02 · Bài toán / baseline"]
    D2 --> D3["03 · ReAct / tool calling"]
    D3 --> D4["04 · Context / state / harness"]
    D4 --> D5["05 · Yêu cầu / tiêu chí đầu ra"]
    D5 --> D6["06 · Giả thuyết / thử nghiệm"]
    D6 --> D7["07 · Chunk / embedding / index"]
    D7 --> D8["08 · RAG / rerank / grounding"]
    D8 --> D9["09 · Memory / skill / MCP / A2A / multi-agent"]
    D9 --> D10["10 · Data pipeline / freshness / lineage"]
    D10 --> D11["11 · Guardrails / HITL / red team"]
    D11 --> D12["12 · Runtime local / restart / retry / queue"]
    D12 --> D13["13 · Logs / metrics / traces"]
    D13 --> D14["14 · Eval / judge / regression"]
    D14 --> D15["15 · Routing / cache / chi phí / tổng kết"]
```

Eval, log và giới hạn có từ bản đầu; các chương sau đào sâu và so sánh.
MCP, A2A và nhiều worker là bài thực nghiệm; chỉ giữ khi có ích.

## 2. Một lượt chạy agent

```mermaid
flowchart TD
    Q[Câu hỏi] --> U[Hiểu câu hỏi]
    U -- Mơ hồ --> H[Hỏi lại]
    H --> U
    U --> P[Lập truy vấn]
    P --> C[Chọn context + state]
    C --> G[Harness: schema + quyền + budget]
    G --> T[Tool calling]
    T --> PM[PubMed]
    T -. Nguồn mở rộng .-> R[Medical RAG]
    PM --> E[Phân tích bằng chứng]
    R --> E
    E --> V[Đối chiếu trích dẫn]
    V --> D{Đủ bằng chứng?}
    D -- Chưa đủ và còn budget --> P
    D -- Đủ --> F[Báo cáo có nguồn]
    D -- Hết budget --> L[Báo phần thiếu + dừng]
    T -- Lỗi API --> X[Ghi lỗi + retry giới hạn]
    X --> D
    G -- Không hợp lệ --> L
```

## 3. Vòng học mỗi tính năng

```mermaid
flowchart LR
    A[Hiểu lý do] --> B[Build phần nhỏ]
    B --> C[Chạy một ca]
    C --> D[Cố ý tạo lỗi]
    D --> E[Đọc trace]
    E --> F[Sửa + kiểm tra lại]
    F --> G[Ghi điều đã học]
```

**Các ca mô phỏng:** Alzheimer hoàn tất · COPD hỏi lại · ung thư phổi tìm bổ sung · PubMed timeout · tài liệu chứa injection.

**Đang học:** chạy [hệ thống tích hợp](system.md), quan sát quyết định và đọc trace.
Các bài nhỏ, gồm [vòng lặp hai tool](7.md), được giữ để học từng thành phần.
**Chi tiết trách nhiệm và nguồn:** [workflow.md](workflow.md).
