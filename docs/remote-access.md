# Truy cập SagaReel từ xa một cách an toàn

Mặc định SagaReel chỉ nghe ở `127.0.0.1:8990`: chỉ chính máy bạn vào được và không cần đăng nhập. Tài liệu này hướng dẫn
mở app ra ngoài (điện thoại, laptop khác, agent ở máy khác) mà không để lộ máy cho cả Internet.

Nguyên tắc: **đặt mật khẩu trước, mở cổng sau, và luôn đi qua một tunnel có HTTPS** (Cloudflare Tunnel hoặc Tailscale).
Đừng mở cổng router trỏ thẳng vào máy, và đừng chạy `sagareel serve --host 0.0.0.0` để "cho tiện".

## 1. Cách app phân biệt "máy nhà" và "từ xa"

Một yêu cầu được coi là **máy nhà** khi đồng thời: đến từ địa chỉ loopback, header `Host` đúng là `127.0.0.1:8990`,
`localhost:8990` hoặc `[::1]:8990`, và **không** có header chuyển tiếp nào (như `X-Forwarded-For`, `CF-Connecting-IP`).
Tunnel luôn thêm header chuyển tiếp nên mọi truy cập qua tunnel bị coi là **từ xa**, dù tunnel chạy ngay trên máy bạn.

Ba lớp bảo vệ chạy ở mọi yêu cầu:

1. **Host tin cậy.** Tên miền trong yêu cầu phải nằm trong danh sách cho phép, nếu không trả `untrusted_host` (400).
2. **Xác thực.** Máy nhà tự là chủ (nếu bạn chưa bật bắt đăng nhập); từ xa phải có phiên đăng nhập (cookie) hoặc token API (`Authorization: Bearer`).
3. **CSRF.** Yêu cầu ghi từ trình duyệt phải có `Origin` thuộc danh sách tin cậy và mã chống giả mạo; yêu cầu "cross-site"
   bị chặn với mọi người dùng.

Trang giao diện luôn tải được (để hiện màn đăng nhập); mọi API (`/api`), media (`/media`) và tài liệu API đều cần xác thực.

## 2. Các cài đặt truy cập

Vào **Cài đặt > Truy cập** (chỉ chủ sở hữu thấy). Thay đổi có hiệu lực ngay, không cần khởi động lại.

| Khoá cài đặt | Ý nghĩa | Mặc định |
|---|---|---|
| `remote.public_hosts` | Danh sách tên miền công khai được phép vào, mỗi dòng một tên (vd. `studio.example.com`) | trống (chỉ máy nhà) |
| `remote.require_login_local` | Bắt đăng nhập cả khi vào từ máy nhà. Chỉ có tác dụng khi đã đặt mật khẩu chủ | bật |
| `remote.allow_viewer` | Cho phép tài khoản chỉ xem (cần đặt thêm mật khẩu người xem) | tắt |
| `remote.client_ip_header` | Tên header do tunnel ghi chứa IP thật của khách, dùng để giới hạn đăng nhập sai theo IP | `cf-connecting-ip` |

Ghi chú:
- `remote.public_hosts` nhận `host`, `host:port` hoặc cả URL; app lấy phần tên host. Muốn dùng thêm IP LAN thì ghi IP vào
  đây.
- Nên **để `remote.require_login_local` bật**. Reverse proxy kiểu nginx `proxy_pass`, `ssh -L`, `socat`, `frp` chế độ TCP...
  không gắn header chuyển tiếp nên trông y hệt máy nhà; khi bật tuỳ chọn này mọi truy cập đều phải đăng nhập.
- `remote.client_ip_header` chỉ được tin khi kết nối đến từ loopback (tunnel trên cùng máy). Để trống thì app chỉ biết IP kết
  nối (tức loopback) và chỉ **làm chậm** việc dò mật khẩu chứ không khoá theo từng IP.

## 3. Đặt mật khẩu trước tiên

1. Ngay trên máy chạy SagaReel, mở `http://127.0.0.1:8990`, vào **Cài đặt > Truy cập > Mật khẩu**.
2. Đặt **mật khẩu chủ sở hữu** (tối thiểu 8 ký tự). Mật khẩu **lần đầu chỉ đặt được trên máy nhà**, nên người ngoài không
   thể "đến trước" bạn.
3. (Tuỳ chọn) Đặt **mật khẩu người xem** (khác mật khẩu chủ), rồi bật `remote.allow_viewer`.
4. Từ giờ app hỏi mật khẩu cả khi bạn vào bằng `127.0.0.1` (do `remote.require_login_local` bật).

Đổi mật khẩu một vai trò sẽ đăng xuất mọi thiết bị và thu hồi mọi token API của vai trò đó. Đổi mật khẩu cần nhập mật khẩu
chủ hiện tại. Đăng nhập sai quá 5 lần trong một phút từ cùng một IP thì bị khoá tạm (429, `too_many_attempts`).

Phiên đăng nhập kéo dài 12 giờ; tick "nhớ thiết bị" thì 30 ngày. Cookie phiên là `HttpOnly`, và tự có cờ `Secure` khi
truy cập qua HTTPS hoặc qua một host công khai.

## 4. Người xem làm được gì, không làm được gì

| Vai trò | Làm được | Không làm được |
|---|---|---|
| **Chủ sở hữu** (owner) | Mọi thứ trong giao diện, kể cả duyệt gate, tạo token, đổi cài đặt | Đặt mật khẩu lần đầu từ xa (chỉ máy nhà) |
| **Người xem** (viewer) | Xem dự án, tiến độ, ảnh, video (xem trực tiếp và thumbnail); giao diện ẩn các nút ghi | Sửa, tạo, tạo lại, duyệt/trả gate, xoá, tải file media gốc, vào Truy cập/token/phiên. Mọi thao tác ghi bị từ chối |

Người xem không bao giờ xuất hiện trong lịch sử thay đổi (không ghi được). Tắt `remote.allow_viewer` cắt ngay các phiên và
token của người xem (tối đa vài giây). Nếu bạn cần gửi bản video cho người khác, hãy xuất gói rồi gửi file, thay vì cho họ quyền.

## 5. Cloudflare Tunnel trên Windows (khuyến nghị cho truy cập công khai)

Cần: một tên miền đã quản lý ở Cloudflare (miễn phí), và SagaReel đang chạy (`run.bat`). Dưới đây dùng
`studio.example.com` làm ví dụ; thay bằng tên của bạn.

1. **Cài cloudflared** (PowerShell):
   ```powershell
   winget install --id Cloudflare.cloudflared
   ```
2. **Đăng nhập** vào Cloudflare, chọn đúng tên miền khi trình duyệt mở:
   ```powershell
   cloudflared tunnel login
   ```
3. **Tạo tunnel** và ghi lại mã UUID in ra:
   ```powershell
   cloudflared tunnel create sagareel
   ```
4. **Trỏ DNS** cho tên miền con:
   ```powershell
   cloudflared tunnel route dns sagareel studio.example.com
   ```
5. **Viết file cấu hình** `%USERPROFILE%\.cloudflared\config.yml`:
   ```yaml
   tunnel: <UUID-của-tunnel>
   credentials-file: C:\Users\<tên-bạn>\.cloudflared\<UUID-của-tunnel>.json
   ingress:
     - hostname: studio.example.com
       service: http://127.0.0.1:8990
     - service: http_status:404
   ```
   Giữ `service` trỏ về `127.0.0.1:8990`. Không đổi `Host` gửi về app: app cần thấy `studio.example.com` để đối chiếu
   danh sách host tin cậy.
6. **Khai báo cho SagaReel** (làm trên máy nhà, Cài đặt > Truy cập):
   - `remote.public_hosts`: thêm dòng `studio.example.com`.
   - `remote.client_ip_header`: để `cf-connecting-ip` (Cloudflare luôn ghi đè header này bằng IP thật của khách).
   - `remote.require_login_local`: để bật.
7. **Chạy tunnel**:
   ```powershell
   cloudflared tunnel run sagareel
   ```
   Muốn tự chạy khi mở máy: `cloudflared service install` (PowerShell quyền Administrator). Nếu dịch vụ không thấy cấu hình,
   sao chép `config.yml` và file `.json` vào thư mục `.cloudflared` của tài khoản hệ thống theo hướng dẫn của Cloudflare.
8. **Thử từ điện thoại bằng 4G** (không dùng Wi-Fi nhà): mở `https://studio.example.com`, phải thấy màn đăng nhập. Thử
   mật khẩu sai vài lần để chắc rằng bị chặn đúng.

Gợi ý thêm:
- Bật thêm **Cloudflare Access** (đăng nhập bằng email/Google trước khi tới app) cho lớp bảo vệ thứ hai. Khi đó agent dùng token
  API sẽ cần cách vượt Access riêng (service token của Cloudflare), nên chỉ bật nếu bạn không dùng agent từ xa.
- Đọc điều khoản của Cloudflare về phát video qua tunnel; nếu bạn lo về điều này hoặc xem video nặng thường xuyên, dùng
  Tailscale (mục 6).
- "Quick tunnel" (`cloudflared tunnel --url ...`) cho tên miền ngẫu nhiên đổi mỗi lần chạy; mỗi lần phải thêm lại vào
  `remote.public_hosts`, nên chỉ dùng thử nhanh.

## 6. Cách thay thế: Tailscale

Tailscale tạo mạng riêng giữa các thiết bị của bạn; chỉ máy đã đăng nhập Tailscale vào được. Không cần tên miền, không
công khai ra Internet.

1. Cài [Tailscale](https://tailscale.com/download) trên máy chạy SagaReel **và** trên điện thoại/laptop cần vào; đăng nhập
   cùng một tài khoản.
2. Trong trang quản trị Tailscale bật MagicDNS và HTTPS certificates.
3. Trên máy chạy SagaReel (PowerShell):
   ```powershell
   tailscale serve --bg 8990
   ```
   Lệnh này công bố `http://127.0.0.1:8990` tại `https://<tên-máy>.<tailnet>.ts.net` chỉ trong tailnet của bạn. **Không dùng
   `tailscale funnel`** trừ khi bạn thực sự muốn công khai ra Internet.
4. Thêm `<tên-máy>.<tailnet>.ts.net` vào `remote.public_hosts`.
5. `remote.client_ip_header`: Tailscale không dùng `cf-connecting-ip`. Nếu không chắc header nào, đặt **trống** (an toàn,
   chỉ làm chậm dò mật khẩu), hoặc kiểm tra header thật mà `tailscale serve` gắn rồi đặt đúng tên. Trong tailnet riêng
   nguy cơ dò mật khẩu thấp hơn nhiều.
6. Mật khẩu vẫn bắt buộc: đừng bỏ vì "đã có Tailscale".

## 7. Upload file lớn qua tunnel

Tunnel thường giới hạn kích thước một yêu cầu (Cloudflare gói miễn phí khoảng 100 MB). SagaReel tải lên theo **khối 8 MB**, nối
lại được: giao diện tự chia file, nếu đứt mạng thì tiếp tục từ chỗ đã nhận. Giới hạn tổng theo loại file: ảnh 50 MB, âm
thanh 500 MB, video 4 GB, phụ đề 10 MB, file bảng nhập hàng loạt 50 MB (nội dung nhập tối đa 20 MB). Xem và tải video hỗ trợ tua
(HTTP Range). Nếu một tập rất lớn, nên xuất gói rồi tải gói xuống thay vì xem trực tiếp qua đường truyền yếu.

## 8. Token API cho agent ở máy khác

Agent ở **cùng máy** không cần gì thêm (xem [agent và MCP](agents-va-mcp.md)). Agent ở máy khác cần token:

- **Tạo**: Cài đặt > Truy cập > **Token API** > đặt tên (chữ, số, `_ . -`, tối đa 64 ký tự; sẽ hiện là `agent:<tên>` trong lịch
  sử), chọn vai trò (chủ sở hữu hoặc người xem), số ngày hết hạn (nên đặt). Token dạng `sr_...` **chỉ hiện đúng một lần**:
  sao chép và lưu vào nơi an toàn.
- **Dùng**: gửi header `Authorization: Bearer <token>`. Với MCP/CLI đặt biến môi trường `SAGAREEL_TOKEN` và `SAGAREEL_URL`
  (`https://studio.example.com`).
- **Thu hồi**: bấm **Thu hồi** cạnh token; dùng lại sẽ bị từ chối ngay.
- **Tạm dừng**: nút tạm dừng agent ở chip Agent/panel Hoạt động tạm khoá một agent (hoặc **mọi** agent, như nút dừng khẩn).
  Agent bị khoá nhận lỗi `token_suspended` (403). Chỉ chủ ở giao diện hoặc máy nhà mới bật lại được; token API không tự bật
  lại, và không tạo được token mới hay đặt mật khẩu.
- Token vai trò người xem chỉ đọc. Token `local-agent` (file `data/local-agent.token`) chỉ dùng được trên máy nhà, qua tunnel
  là bị từ chối.
- Đổi mật khẩu chủ hoặc đặt lại mật khẩu ngoại tuyến sẽ thu hồi mọi token.

Không dán token vào chat, repo, ảnh chụp màn hình hay tin nhắn.

## 9. Quên mật khẩu hoặc nghi bị xâm nhập: đặt lại ngoại tuyến

Ai vào được thư mục `data/` của máy cũng là chủ máy, nên có lệnh đặt lại không cần đăng nhập:

1. **Tắt SagaReel** (lệnh sẽ từ chối với `app_running` nếu app còn chạy).
2. Trong thư mục gốc repo:
   ```powershell
   .venv\Scripts\python -m sagareel reset-password
   ```
   Lệnh hỏi mật khẩu mới hai lần (ẩn). Tuỳ chọn: `--role viewer` (chỉ người xem), `--role viewer --clear` (xoá mật khẩu
   người xem), `--keep-viewer` (giữ mật khẩu người xem), `--data-dir PATH` (nếu dùng thư mục dữ liệu khác).
3. Đặt lại mật khẩu **chủ** là đường khôi phục sau sự cố: nó thu hồi **mọi phiên và mọi token API**, xoá mật khẩu người xem
   (trừ khi `--keep-viewer`) và tạo lại token agent cục bộ. Lệnh chỉ in số lượng đã thu hồi, không in mật khẩu.
4. Mở lại SagaReel và đăng nhập bằng mật khẩu mới; tạo lại token cho các agent cần dùng.

## 10. Danh sách kiểm tra an toàn

- [ ] Đã đặt mật khẩu chủ (mạnh, không dùng lại ở nơi khác) **trước** khi thêm host công khai.
- [ ] `remote.require_login_local` đang bật.
- [ ] `remote.public_hosts` chỉ chứa đúng các tên miền bạn dùng.
- [ ] SagaReel vẫn nghe ở `127.0.0.1` (mặc định của `run.bat`); không dùng `--host 0.0.0.0`, không chuyển cổng ở router.
- [ ] Truy cập từ xa đi qua Cloudflare Tunnel hoặc Tailscale (HTTPS), không dùng `http://IP-công-cộng:8990`.
- [ ] `remote.client_ip_header` khớp tunnel đang dùng (`cf-connecting-ip` cho Cloudflare).
- [ ] Đã thử từ mạng ngoài: thấy màn đăng nhập, mật khẩu sai nhiều lần bị chặn.
- [ ] Mật khẩu người xem khác mật khẩu chủ; chỉ bật `remote.allow_viewer` khi cần.
- [ ] Mỗi agent từ xa có token riêng, đặt hạn, đặt tên dễ nhận; token cũ đã thu hồi.
- [ ] Biết cách dừng khẩn: tạm dừng mọi agent, thu hồi phiên và token, hoặc tắt tunnel.
- [ ] Biết cách đặt lại mật khẩu ngoại tuyến (mục 9).
- [ ] Không chia sẻ file `.env.local` (chứa khoá nhà cung cấp) và thư mục `data/` (chứa cơ sở dữ liệu và khoá đã lưu).
- [ ] Ngân sách ảnh/video đã đặt hợp lý để một agent hoặc kẻ lạ lọt vào cũng không đốt hết hạn mức.
- [ ] Máy chạy SagaReel được cập nhật Windows và không để người lạ dùng chung tài khoản.

## 11. Lỗi thường gặp

| Hiện tượng | Nguyên nhân và cách xử lý |
|---|---|
| `untrusted_host` (400) | Tên miền chưa nằm trong `remote.public_hosts`. Thêm từ máy nhà |
| `auth_required` (401) | Chưa đăng nhập hoặc token sai/hết hạn/bị thu hồi |
| `csrf_failed` (403) | Mở app từ trang lạ hoặc cookie cũ; tải lại trang. Tên miền truy cập phải đúng bằng host đã khai báo |
| `too_many_attempts` (429) | Nhập sai mật khẩu quá nhiều; chờ theo số giây trong thông báo |
| `token_suspended` (403) | Agent đang bị tạm dừng; chủ bật lại trong giao diện |
| Mật khẩu lần đầu không đặt được từ xa | Đúng thiết kế: đặt trên máy nhà |
| Danh sách phiên đăng nhập hiện mọi khách cùng một IP (127.0.0.1) | `remote.client_ip_header` sai hoặc bỏ trống; chỉnh đúng header của tunnel |
