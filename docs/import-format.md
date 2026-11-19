# Định dạng nhập clip hàng loạt

Nhập nhiều clip một lần vào một tập (sequence). Luôn đi qua hai bước: **xem trước** (`import.preview`, không ghi gì) rồi **áp dụng** (`import.apply`, tạo tất cả trong một lần, hoàn tác được bằng một thao tác).

Mẫu có sẵn (cùng một ví dụ phim ngắn 6 clip, 2 cảnh): `docs/import-templates/clips.csv`, `clips.xlsx`, `clips.json`, `clips.yaml`.

## Nguồn nhập (`source.kind`)

| kind | Dữ liệu | Ghi chú |
|---|---|---|
| `lines` | `text`: dán, mỗi dòng một clip | Xem mục "Dán dòng" |
| `csv` | `text` hoặc `upload_id` | UTF-8 (có/không BOM), phân tách `,` `;` hoặc Tab, xuống dòng Windows hoặc Unix |
| `xlsx` | `upload_id` (kind upload `other`) | Sheet đầu (hoặc `sheet`), hàng đầu là tiêu đề, hàng trống bị bỏ qua |
| `json` | `text` hoặc `upload_id` | Danh sách clip, hoặc `{"clips": [...]}`, hoặc `{"scenes": [{"scene": "...", "clips": [...]}]}` |
| `yaml` | `text` hoặc `upload_id` | Cùng cấu trúc với JSON |
| `folder` | `media_ids` | Mỗi ảnh một clip (`input_media_id`), sắp theo tên file (shot2 trước shot10) |

Tối đa 5000 hàng mỗi lần; file nhập tối đa 20 MB. Tải file lớn qua upload chia khối (`upload.start` → `PUT /api/upload/{id}?offset=` → `upload.finish`).

## Các cột

Tên cột không phân biệt hoa/thường và dấu: `Cảnh`, `canh`, `CẢNH`, `Scene` đều được. Cột không nhận ra bị bỏ qua kèm cảnh báo; cột đánh số (`STT`, `No`, `#`, `ID`) bị bỏ qua im lặng. Có thể chọn lại bằng tham số `mapping` (`{"tên cột": "trường"}`; `null` = bỏ qua).

| Trường | Tên cột nhận (ví dụ) | Giá trị |
|---|---|---|
| `scene` | Cảnh, Scene | `Cảnh 2: Ban công` hoặc `2` hoặc tên tự do. Ô trống = vẫn cảnh của hàng trên |
| `title` | Tiêu đề, Tên, Title | Tên ngắn của clip |
| `characters` | Nhân vật, NV, Characters | Mã asset cách nhau bởi `,` `;` hoặc xuống dòng. `AN:áo_mưa` = nhân vật AN, look áo_mưa (khớp khoá hoặc tên look, không phân biệt dấu) |
| `image_prompt` | Prompt ảnh, Image, Mô tả, Prompt | Prompt ảnh (giữ nguyên văn) |
| `motion_prompt` | Prompt video, Motion, Chuyển động | Prompt chuyển động |
| `dialogue` | Thoại, Lời thoại, Dialogue | Mỗi dòng một câu: `AN: Anh ấy lại không đến.` Thêm cảm xúc: `AN(giận): ...`. Câu không có tiền tố dùng người nói ở cột `speaker`, không có thì là người kể. `NR:` = người kể |
| `speaker` | Người nói, Speaker | Người nói mặc định cho câu không có tiền tố |
| `duration` | Thời lượng, Duration, Giây | `6`, `6,5`, `6s`, `6 giây`, `0:06`, `6000ms` (số trần = giây). Từ 0,5 đến 600 giây; sai thì hàng đó báo lỗi, các hàng khác vẫn nhập |
| `link` | Nối, Link, Transition | `cut` (cắt) / `fade` (mờ) / `continue` (nối tiếp, dùng khung cuối clip trước) |
| `engine` | Engine, Động cơ | `ai_video` / `still_motion` (ảnh động) / `code_anim` |
| `connection` | Provider, Kết nối | Tên kết nối provider (lưu ở `clip.settings.connection`) |
| `model` | Model, Mô hình | Tên model (lưu ở `clip.settings.model`) |
| `prompt_mode` | Chế độ prompt | `verbatim` (nguyên văn) / `synced` (đồng bộ) |
| `shot_size` | Cỡ cảnh, Shot size | Toàn cảnh, Trung cảnh, Cận cảnh... (văn bản tự do) |
| `camera_move` | Chuyển động máy, Camera | Dolly in, Pan phải... |
| `notes` | Ghi chú, Notes | Ghi chú tự do |
| `input_media_id` | Media đầu vào | Id ảnh trong kho media (dùng cho `folder`) |

Trong JSON/YAML, tên khoá cũng theo bảng trên (tiếng Anh hoặc Việt). `characters` và `dialogue` có thể là danh sách; mỗi câu thoại có thể là `{"speaker": "AN", "text": "...", "emotion": "giận"}`.

## Dán dòng (`lines`)

```
Cảnh 1: Quán cà phê đêm mưa
Toàn cảnh quán cà phê nhỏ ven phố, đèn vàng ấm
Cận cảnh An ngồi bên cửa sổ, ôm ly cà phê nguội
AN: Anh ấy lại không đến.
- Minh đẩy cửa bước vào
MINH(thở dốc): Xin lỗi, anh đến muộn.
Cảnh 2: Ban công
Hai người nhìn thành phố sau cơn mưa
NR: Đêm mưa ấy trở thành kỷ niệm.
```

- Mỗi dòng là một clip (prompt ảnh); gạch đầu dòng `-`, `*`, `1.` được tự bỏ. Dòng bắt đầu bằng `//` là chú thích.
- `Cảnh X: tên` (hoặc `Cảnh X`, `Scene X: ...`) là tiêu đề cảnh: các clip sau thuộc cảnh đó.
- Dòng dạng `MÃ: câu` là thoại gắn vào clip ngay trước. Được coi là thoại khi `MÃ` là một từ viết hoa (`AN`, `MINH`), trùng mã/tên asset đang có, hoặc là `NR` (người kể). Vì vậy dòng như `Cận cảnh: bàn tay` vẫn là clip.

## Asset và look

- Mã nhân vật khớp với **khoá hoặc tên** asset trong dự án (không phân biệt dấu/hoa thường). Look khớp theo khoá hoặc tên look; look không có thì dùng look mặc định (có cảnh báo).
- Mã chưa có trong thư viện được liệt kê ở `assets_to_create`. `import.apply(create_assets=true)` tạo asset nhân vật mới theo mã; để `false` thì mã đó bị bỏ qua (kết quả trả `unresolved_assets`).

## Xem trước → sửa → áp dụng

`import.preview` trả `columns`, `mapping` (đã đoán), `rows[{index, source_row, fields, errors, warnings}]`, `assets_to_create`, `warnings`, `preview_token` (sống 1 giờ, dùng một lần).

`import.apply(sequence_id, preview_token, edits?, create_assets?, after_clip_id?, skip_rows?, skip_errors?)`:

- `edits`: `[{"index": 3, "fields": {"duration": "6", "characters": "AN, MINH"}}]`, giá trị thô như trong file.
- Còn hàng lỗi mà không `skip_errors` thì từ chối cả lô (`import_has_errors`, liệt kê hàng); `skip_errors=true` bỏ qua các hàng lỗi, `skip_rows` bỏ hàng theo `index`.
- `after_clip_id`: chèn ngay sau clip đó; bỏ trống = nối cuối tập. Thứ tự dùng `rank`, không đụng clip khác.
- Cảnh chưa có được tạo; cảnh trùng khoá được dùng lại. Mỗi clip có khoá `c001`, `c002`... tiếp nối khoá đã dùng (kể cả clip đã xoá).

## Mã lỗi thường gặp

`import_parse_failed`, `import_format_invalid`, `import_file_invalid` (XLSX hỏng), `import_mapping_invalid`, `import_too_many_rows`, `import_has_errors`, `import_nothing_to_apply`, `preview_token_invalid` (đã áp hoặc hết hạn), `sequence_not_found`.
