# Quy trình đánh giá và chấm nội dung

Phân biệt ba loại bằng chứng: tests xác định chứng minh hành vi phần mềm trên
fixtures; smoke test thật xác nhận kết nối và một số luồng chạy; benchmark có nhãn
mới đo độ đáp ứng nội dung. Không suy ra chất lượng y khoa từ tỷ lệ tests đạt.

## 1. Dữ liệu đánh giá hiện có

- [research_cases.json](../evals/research_cases.json): ba yêu cầu do người học cung cấp.
- [questions.json](../evals/questions.json): 30 câu hỏi đề xuất, 10 dev và 20 test,
  10 câu mỗi chủ đề. Chưa có nhãn nguồn hoặc đáp án y khoa được rà độc lập.
- [safety_cases.json](../evals/safety_cases.json): 12 tình huống phần mềm có test ID
  thực thi được, dùng fixture/mock và không phải chứng nhận an toàn với model thật.

Split trong questions còn là bản nháp. Trước thí nghiệm cuối cần nhóm lại câu hỏi
gần trùng và tài liệu tham chiếu giao nhau. Runner từ chối group giao dev/test và
nguồn tham chiếu đã chấm xuất hiện ở cả hai tập. Không đánh dấu `references_reviewed`
khi chỉ lấy bài đứng đầu kết quả hoặc dùng model tự gán nhãn mà chưa kiểm tra.

## 2. Gán nhãn tham chiếu trước thí nghiệm

Mỗi câu cần người đọc kiểm tra phạm vi, `required_outputs`, tài liệu tham chiếu
thực sự có trong snapshot và đoạn chứng cứ. Điền `reference_source_ids` bằng ID
trong manifest, rồi ghi người chấm và thời điểm vào hồ sơ annotation của dự án.
Chỉ bật `references_reviewed=true` sau khi hoàn tất. Tài liệu và reviewer chưa có
thì giữ false, không tự tạo số liệu retrieval.

Một người chấm toàn bộ; người thứ hai kiểm tra tối thiểu 20% được chọn trước.
Ghi khác biệt và cách thống nhất. Nếu không có người có chuyên môn y sinh, chỉ
tuyên bố đã chấm độ hỗ trợ theo văn bản và yêu cầu chức năng.

Nhãn tham chiếu, rubric và `required_outputs` không được gửi cho model. Runner chỉ
gửi câu hỏi và trả lời làm rõ đã khai báo; engine tự tạo tiêu chí từ yêu cầu.

## 3. Chạy A/B trên snapshot đóng băng

```bash
python3 -m medical_agent.benchmark run \
  --corpus outputs/corpus/pubmed-demo.json \
  --cases evals/questions.json --split dev \
  --output outputs/experiments/dev-v1
```

Mặc định chạy fixed trước rồi agent cho mỗi câu; đảo thứ tự ở lần lặp chẵn.
Hai phương án dùng cùng model, corpus, schema báo cáo và giới hạn. Fixed dùng
query trong plan, đọc tối đa top-k rồi dừng, không gọi model quyết định bước tiếp.
Agent tự điều chỉnh và có chi phí quyết định riêng. Model có thể tạo plan khác
giữa hai lượt; lưu plan để phân tích yếu tố này, không giả định mọi query giống hệt.

Mỗi variant/câu/lần lặp có một runtime mới. Runner lưu code hash, corpus hash,
cases hash, cấu hình, môi trường và model tag/digest nếu Ollama cung cấp. Không
ghi đè thí nghiệm đã có. Nếu ngắt, giữ kết quả đã hoàn tất và checkpoint run đang chạy.

Để chạy một ca: thêm `--case alz_sleep_single`. Đo ổn định: chọn trước ca đại diện
và dùng `--repeats 3`. Sau khi khóa thiết kế và nhãn, mới dùng `--split test`.
Chạy test rồi chỉnh prompt phải được ghi nhận; cần vòng xác nhận trên dữ liệu chưa dùng để chỉnh.

## 4. Chấm report sau mỗi thí nghiệm

Copy `reviews.template.json` thành file review làm việc. Mở report trong runtime
được chỉ ở `results.jsonl`, đối chiếu từng claim với evidence và phần tài liệu đã đọc.

| Nhãn claim | Rubric |
|---|---|
| `supported` | Nội dung, điều kiện, mức chắc chắn, số liệu và chiều quan hệ đều được nguồn đính kèm hỗ trợ |
| `partial` | Nguồn hỗ trợ một phần nhưng thiếu điều kiện hoặc diễn giải quá mức ở một phần claim |
| `unsupported` | Nguồn không hỗ trợ, trái nghĩa, nhầm thuật ngữ/số liệu hoặc suy diễn ngoài nguồn |
| `uncertain` | Phần tài liệu truy cập được chưa đủ để quyết định |

Điền `reviewer`, `reviewed_at`, nhãn mỗi claim, ghi chú và nhãn từng tiêu chí
`covered/missing/uncertain`. Giữ nguyên text, hash và ID; công cụ từ chối claim bị
sửa hoặc bị bỏ khỏi review. `important=false` chỉ dùng khi có lý do trong note,
không dùng để loại các nhận định sai khỏi mẫu số.

`content_complete=true` yêu cầu đã phủ đủ tiêu chí và chấm xong các claim quan trọng;
người chấm vẫn phải đọc tổng thể report. Không tự điền true từ trạng thái runtime.

```bash
python3 -m medical_agent.benchmark score \
  --results outputs/experiments/dev-v1/results.jsonl \
  --reviews outputs/experiments/dev-v1/reviews.completed.json \
  --output outputs/experiments/dev-v1/summary-reviewed.csv
```

Để trống nhãn giữ metric ở null, không chuyển thành 100%. Có review nhưng không
ghi người chấm thì bị từ chối. Review CLI và review benchmark được lưu riêng;
kiểm tra bằng AI không tự biến thành nhãn độc lập của con người.

## 5. Diễn giải metric

| Trường kết quả | Cách đọc |
|---|---|
| `runtime_completed` | Số run đạt hợp đồng cấu trúc, không phải nội dung đúng |
| `structural_pass` | Các invariant ID, quote và budget được giữ; run partial vẫn có thể đạt |
| `content_reviewed`, `content_completed` | Mẫu số đã chấm và số report đạt; công bố thêm số run chưa chấm |
| `claim_support` | Tổng claim quan trọng supported / tổng claim quan trọng trong run đã chấm đủ |
| `mean_claim_support` | Trung bình tỷ lệ theo run, khác tỷ lệ gộp nếu số claim mỗi run khác nhau |
| `precision_at_5`, `recall_at_5` | Chất lượng top-5 ở truy vấn đầu trên tập nguồn tham chiếu đã rà |
| `candidate_recall` | Độ phủ nguồn tham chiếu qua hợp các truy vấn; chưa chứng minh nguồn được đọc đúng |
| `input_tokens`, `output_tokens` | Tổng usage nếu tất cả model calls được ghi đầy đủ; thiếu thì null |
| Latency | Thời gian thực theo run; báo rõ cỡ mẫu, phần cứng và trạng thái model nóng/lạnh |

Khi không có claim, support là null; completion/coverage vẫn phản ánh thiếu đầu ra.
Precision dùng số nguồn thực trả tới 5 làm mẫu số. Recall chỉ đo trên tập nguồn đã
gán nhãn trong snapshot, không đại diện toàn PubMed. Corpus một bài chỉ đủ smoke
test, không dùng để khẳng định lợi ích agent trên ba chủ đề.

## 6. Ngưỡng nghiệm thu đề xuất

Sau khi có đủ nhãn, mục tiêu: hoàn thành nội dung ít nhất 18/20 câu test, citation
hợp lệ 100%, claim support ≥90%, và toàn bộ ca lỗi bắt buộc xử lý đúng. Mọi số liệu
cần tử số/mẫu số, nhóm lỗi và ví dụ không đạt. Các ngưỡng này cần chốt với giảng viên;
chưa phải kết quả dự án đã đạt. Kết quả hiện tại xem [implementation-status.md](implementation-status.md).
