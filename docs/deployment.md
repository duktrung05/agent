# Triển khai MediSearch dùng riêng lên VPS

Bộ cấu hình dành cho một VPS Debian/Ubuntu có systemd, Python 3.10+ với SQLite
FTS5, Ollama và Caddy. App phục vụ một tài khoản dùng chung, thư viện/lịch sử chung
và một tác vụ nghiên cứu tại một thời điểm. Chưa có tách dữ liệu từng người dùng,
hàng đợi nhiều worker hoặc đánh giá tải production.

```text
Trình duyệt → HTTPS Caddy :443 → Python 127.0.0.1:8001 → Ollama 127.0.0.1:11434
                                           └── /var/lib/medisearch (SQLite, uploads, reports)
```

Theo [hướng dẫn HTTPS của Caddy](https://caddyserver.com/docs/quick-starts/https),
trỏ bản ghi A của tên miền về IPv4 VPS và chỉ đặt AAAA nếu VPS phục vụ IPv6 thật.
Cho phép TCP 80/443 ở firewall VPS và nhà cung cấp; giữ quyền SSH đang dùng.
Không mở cổng 8001 hoặc 11434 ra Internet. Chọn máy theo khả năng chạy thử model;
CPU có thể chạy nhưng cần đo thời gian với câu hỏi thực tế.

## 1. Đóng gói tại máy phát triển

```bash
python3 -m unittest discover -s tests -v
python3 deploy/package.py --output outputs/deployment/medisearch-vps.tar.gz
scp outputs/deployment/medisearch-vps.tar.gz user@VPS:/tmp/medisearch-vps.tar.gz
```

Thay `user@VPS` bằng SSH host/alias của bạn. Gói dùng allowlist nguồn, frontend,
lessons, fixtures evals, tests, docs và cấu hình triển khai; không lấy `.aws`, `.env`, tài khoản hay dữ liệu
runtime. Gói không ghi đè file đã tồn tại; dùng tên phiên bản khác khi tạo lại.
Nếu muốn giữ lịch sử trên máy local, dùng quy trình backup/restore trong
[runbook](runbook.md), chuyển bản backup riêng qua SSH sau khi rà dữ liệu.

## 2. Chuẩn bị VPS

Các lệnh bên dưới chạy trên VPS; dùng `/opt/medisearch` cho mã nguồn:

```bash
sudo apt update
sudo apt install -y python3 curl ca-certificates
sudo install -d -m 0755 /opt/medisearch
sudo tar -xzf /tmp/medisearch-vps.tar.gz -C /opt/medisearch
cd /opt/medisearch
python3 -m unittest discover -s tests -v
```

Cài Ollama theo [tài liệu Linux chính thức](https://docs.ollama.com/linux), bật
dịch vụ và tải model. Chỉ thực hiện bước cài nếu máy chưa có Ollama:

```bash
curl -fsSL https://ollama.com/install.sh -o /tmp/ollama-install.sh
sudo sh /tmp/ollama-install.sh
sudo systemctl enable --now ollama
ollama pull qwen3:1.7b
```

Cài Caddy bằng [gói chính thức cho Debian/Ubuntu](https://caddyserver.com/docs/install#debian-ubuntu-raspbian).
Không cần cài thư viện Python bên thứ ba hoặc npm.

## 3. Cài dịch vụ và tạo tài khoản

Thay tên miền ví dụ bằng tên miền thật:

```bash
cd /opt/medisearch
sudo bash deploy/install.sh https://research.example.com
sudo systemctl status medisearch --no-pager
```

Script tạo user hệ thống `medisearch`, thư mục dữ liệu, tài khoản `admin` và unit
systemd; mật khẩu được nhập ẩn hai lần, cần 12–256 ký tự. Chỉ lưu salt và scrypt
hash tại `/etc/medisearch/auth.json`, quyền `root:medisearch 0640`. Không đặt mật
khẩu trong dòng lệnh, source, Caddyfile hoặc tin nhắn. Chạy lại script giữ nguyên
tài khoản và `/etc/medisearch/web.env` hiện có.

`/etc/medisearch/web.env` chứa origin HTTPS duy nhất:

```ini
MEDISEARCH_PUBLIC_ORIGIN=https://research.example.com
```

App vẫn bind loopback. Mọi GET/POST qua tên miền cần HTTP Basic authentication;
trình duyệt hiện hộp đăng nhập. Mỗi POST còn cần đúng HTTPS Origin và token của
phiên. `X-Forwarded-Host`/`X-Forwarded-Proto` không thay được origin đã cấu hình.
Không dùng cách sửa proxy để xóa Origin hoặc giả Host localhost.

## 4. Bật HTTPS

Thêm block dưới vào `/etc/caddy/Caddyfile`, giữ các site khác nếu VPS đã phục vụ web:

```caddyfile
research.example.com {
    reverse_proxy 127.0.0.1:8001
}
```

Đã có mẫu tại `deploy/Caddyfile`. Nếu dùng port HTTPS khác 443, cấu hình cùng port
trong site Caddy và `MEDISEARCH_PUBLIC_ORIGIN`. Caddy giữ Host của request khi proxy
HTTP theo [tài liệu reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#defaults).

```bash
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

Khi DNS và cổng 80/443 hoạt động, Caddy tự lấy/gia hạn chứng chỉ theo
[cơ chế HTTPS tự động](https://caddyserver.com/docs/quick-starts/https).

## 5. Xác nhận triển khai

```bash
curl -i https://research.example.com/api/bootstrap
curl -u admin https://research.example.com/api/health
```

Lệnh đầu phải trả `401` kèm `WWW-Authenticate`, không lộ thư viện/token. Lệnh hai
hỏi mật khẩu trong terminal và cần trả `available: true`. Không dùng `curl -k`
để bỏ kiểm tra chứng chỉ. Mở web, đăng nhập, nhập corpus hợp lệ ở Thư viện nếu
thư mục dữ liệu mới, rồi chạy câu hỏi nhỏ. Xác nhận tiến độ, chứng cứ, tải báo cáo,
lịch sử, dừng/resume và thư viện sau khi restart. Kiểm tra upload qua trình duyệt
và PubMed online riêng; unit tests dùng fixtures, không xác minh nội dung y khoa.

```bash
sudo journalctl -u medisearch -n 100 --no-pager
sudo journalctl -u caddy -n 100 --no-pager
sudo systemctl restart medisearch
```

Dịch vụ gửi SIGINT khi dừng và đợi model đang chạy trả về để lưu checkpoint, tối
đa khoảng 195 giây. Sau restart, tải lại trang để lấy token mới và mở phiên từ lịch
sử để tiếp tục. Không chạy CLI nghiên cứu hoặc server thứ hai trên cùng thư mục dữ liệu.

## 6. Backup, cập nhật và đổi mật khẩu

Backup trước khi cập nhật, dùng user của dịch vụ để không sinh file runtime do root sở hữu:

```bash
cd /opt/medisearch
sudo -u medisearch python3 -m medical_agent --home /var/lib/medisearch backup /var/lib/medisearch/backups/before-update-v1
sudo systemctl stop medisearch
```

Sau đó giải nén gói phiên bản mới vào `/opt/medisearch` và bật lại dịch vụ. Lưu bản
mã nguồn cũ và backup ra nơi riêng để phục hồi. Khôi phục dữ liệu vào thư mục mới
theo runbook; không ghi đè runtime đang chạy.

Để đổi mật khẩu, tạo file mới bằng lệnh bên dưới. Khi xác nhận file tạo thành công,
thay file hiện hành rồi restart (không thay đổi tài khoản khi app vẫn đang chạy):

```bash
cd /opt/medisearch
sudo python3 -m medical_agent.web --create-auth /etc/medisearch/auth.next.json --username admin
sudo chown root:medisearch /etc/medisearch/auth.next.json
sudo chmod 0640 /etc/medisearch/auth.next.json
sudo systemctl stop medisearch
sudo mv /etc/medisearch/auth.next.json /etc/medisearch/auth.json
sudo systemctl start medisearch
```

HTTP Basic dùng thông tin đăng nhập do trình duyệt lưu; đóng phiên trình duyệt
riêng tư sau demo trên máy dùng chung. Bản này chưa có logout/session management,
khóa tài khoản khi dò mật khẩu hay phân quyền nhiều người; giới hạn người được cấp
tài khoản và dùng VPN/lớp kiểm soát truy cập bổ sung nếu mở rộng phạm vi dùng.

## Trạng thái kiểm tra

Bộ triển khai được chuẩn bị trong repository. Tests xác định kiểm tra đăng nhập,
Host/Origin, CSRF, checkpoint khi dừng và gói nguồn loại secrets. Việc cài systemd,
reverse proxy, DNS, chứng chỉ và một lượt model thật cần được xác nhận trên VPS
đích; không suy ra đã deploy từ kết quả unit tests.

Kiểm tra ngày 29/10/2026: 85/85 tests đạt; cú pháp script và unit systemd đã kiểm
tra. HTTP loopback thật bằng model fixture xác nhận đăng nhập, từ chối sai Origin,
nhập file qua API và tải báo cáo, lưu tại `outputs/validation/deployment-smoke.json`.
Máy phát triển chưa có Caddy để kiểm tra tích hợp proxy/TLS.
