# Thị trường công cụ làm phim AI — bài học cho SagaReel Studio

Ngày nghiên cứu: 2026-11-20. Phương pháp: tìm kiếm web + đọc trang gốc. **Cảnh báo độ tin cậy:** phần lớn nguồn là trang
marketing của chính hãng, blog đối thủ hoặc bài tổng hợp; chưa có benchmark độc lập về độ nhất quán. Mọi con số/khẳng định
dưới đây coi là "hãng tự nói" trừ khi ghi khác. Đã biết sẵn từ `oss-study.md` (không lặp lại): Story Bible, lock word, ảnh mỏ
neo, gate theo tập, lineage, MCP. Không có thông tin trực tiếp về Hedra, Pika (chỉ có tin H3 Flex), Story.com, Storyboarder,
Runway Act/Aleph (chỉ bài hướng dẫn thứ cấp) — các dòng đó ngắn và ghi rõ là mỏng.

## 1. Bảng so sánh theo 6 trục

| Công cụ | (1) Nhất quán nhân vật/địa điểm | (2) Shot list / storyboard | (3) Duyệt/lặp take | (4) Dựng/âm thanh/phụ đề | (5) Hàng loạt / nhiều tập | (6) Khen / chê |
|---|---|---|---|---|---|---|
| **LTX Studio** | "Elements" (nhân vật, vật, địa điểm) trích tự động từ kịch bản, gọi bằng `@` trong shot; khuyên ảnh chính diện; có IC-LoRA/camera LoRA | Kịch bản → cảnh → shot; xem bảng phân rã (số shot, chi tiết) trước khi sinh; lên kế hoạch chuyển động máy | Duyệt storyboard trước khi sinh; xuất PDF để khách duyệt | Có timeline/xuất; chi tiết thứ yếu | Cấp dự án, không thấy cơ chế lô/tập riêng | Khen: quy trình pre-vis liền mạch. Chê/lưu ý: số liệu nhất quán đều do hãng nói |
| **Higgsfield** (Popcorn → Soul ID → Cinema Studio) | Soul ID: train ~20+ ảnh đa dạng/nhân vật (~25 credit); Popcorn: tối đa 4 ảnh vào, "frame memory" | Popcorn: 1 prompt → 4/6/8 khung (Auto) hoặc mô tả từng khung (Manual); dùng khung cuối làm ref cho chuỗi kế để đi tiếp; Cinema Studio coi thể loại/ánh sáng/ống kính/chuyển động máy là tham số rõ ràng | Duyệt storyboard rồi mới xuất sang video model | Xuất sang Sora 2 | Dài hơn 8 khung thì nối bằng khung cuối | Chê: tính phí mỗi lần thử, drift nhận dạng ép người dùng sinh lại; hoàn tiền chỉ khi job lỗi |
| **OpenArt** (Characters/One-Click Story) | Character Builder: lưu nhân vật từ 1 ảnh vào thư viện bền, dùng lại cho mọi ảnh/video; 3 mẫu (Character Vlog, Music Video, Explainer) | Truyện → cảnh; "add new shot" để kéo dài | Sửa từng shot | Có nhạc/giọng trong mẫu | Dài tới ~5 phút bằng cách thêm shot | Khen: mức vào cửa thấp. Chính hãng thừa nhận video vẫn drift |
| **Katalist** | Chọn "diễn viên" từ ảnh ref một lần, đổi diễn viên một bước rồi sinh lại | Từ kịch bản (.fdx/Celtx/Doc) → shot list + ghi chú máy; điều khiển khung hình/ống kính/tư thế mỗi shot | Bước review trước khi xuất | Xuất PDF/PNG/animatic; có tích hợp Premiere/FCP (nguồn không thống nhất) | Hướng tới đội sản xuất | Đối thủ chê yếu nhất quán (chưa kiểm chứng) |
| **Runway** (Gen-4 References, Act-Two, Aleph) | References: ảnh ref sạch, sáng đều; nền lộn xộn/nhiều yếu tố làm giảm độ khoá | Không có storyboard sâu | Act-Two dùng cho cận mặt/biểu cảm, I2V + ref cho toàn thân | Aleph sửa video có sẵn | — | Hoàn tiền tự động khi lỗi sinh; không hoàn khi kết quả xấu |
| **Kling 3.0 / O1 Omni** | **Element Library**: mỗi element tối đa ~4 ảnh (trước/bên/sau/chi tiết), "subject binding" qua nhiều shot | **Custom Multi-Shot**: mỗi shot có độ dài, cỡ cảnh, góc nhìn, nội dung, chuyển động máy; clip ≤ ~15 s | Director Mode; đổi prompt từng shot | Âm thanh gốc (theo mô tả hãng) | Dùng chung thư viện element | Chê: nhất quán xuyên cảnh vẫn là vấn đề mở |
| **Google Flow / Veo 3.1** | "Ingredients to Video" (nhiều ảnh thành phần), Scenebuilder nối cảnh; khuyên tạo ingredient bằng Nano Banana Pro | Scenebuilder (dòng cảnh), 9:16 gốc | Checklist hậu duyệt của cộng đồng: đúng hành động, máy, ánh sáng liền kề, lỗi tay/mặt/chữ/phản chiếu | Có nâng cấp 1080p/4K chỉ trong Flow/API | Flow "đã giúp tạo 100 triệu video" (báo chí) | Chê: "gót chân Achilles" là nhất quán; không có số liệu độc lập |
| **Invideo AI (Agent One)** | Khoá "character sheet" vào ngữ cảnh agent, gắn vào mọi shot, không train LoRA; khuyên bộ ref tối giản kiểu "audition reel": 1 mặt rõ + 4 góc + 1 ref chiều cao (sheet quá phức tạp gây drift) | Agent tự phân cảnh | FAQ: chỉ ~25% clip lọt vào bản cuối | Hậu kỳ trong app | Ví dụ phim 70 s, 2 nhân vật, $750 (tự báo) | Số liệu tự báo, chưa kiểm chứng |
| **Hedra / Pika** | Thông tin mỏng; Pika H3 Flex hướng nhất quán đa shot | — | — | Hedra mạnh ở nhép môi/nhân vật nói (không kiểm chứng ở đây) | Pika thiên về clip ngắn | Chưa đủ dữ liệu độc lập |
| **Krea / Flora / Freepik Spaces** | Canvas node: output thành node, chuỗi hoá workflow; Krea có prompt → workflow | Không có shot grammar riêng, tự dựng | Chỉnh node đầu nguồn, node hạ nguồn chạy song song | Hạn chế dựng | "Nhân bản + tự động hoá workflow" (Spaces) | Khen: linh hoạt. Hạn chế: không có "lớp phim", nặng quy trình thủ công |
| **Boords** (storyboard truyền thống) | — | **Trường tuỳ biến mỗi khung** (nhãn, âm, hành động, ánh sáng, máy; kiểu ngày/dropdown màu/checkbox); view "bảng shot list" xuất Excel; chuyển storyboard→shot list là *snapshot*, sửa sau không tự cập nhật | Trạng thái từng khung, cột "best take" cho editor | Animatic | — | Chuẩn mực nghề: bảng + ảnh + trạng thái |
| **CapCut/Dreamina (Seedance 2.0/2.5)** | Multi-shot liền mạch, ref nhiều ảnh | Kịch bản → phân cảnh bằng LLM → shot | "Chỉnh sửa tinh" thay vì sinh lại cả cảnh | Timeline CapCut, bản dọc, đủ dài | "Seedance 2.5 Bulk Create" (desktop) | Nhiều trang đang lẫn tên bản 2.0/2.5 — kiểm tra bản hiện hành |
| **Chuỗi AI漫剧/短剧 Trung Quốc** (即梦短剧工作室, Tencent Cloud, PixTV...) | Ma trận ảnh ref **theo bối cảnh × cảm xúc × trang phục** sinh tự động; "捏脸" chỉnh mặt/biểu cảm bằng thanh trượt; kho 7000+ diễn viên; (open source) LoRA + ControlNet + IP-Adapter + seed cố định | Kịch bản có cấu trúc JSON `shot_id` (cảnh/lời thoại/cảm xúc); phân cảnh có **cỡ cảnh, chuyển động máy, mô tả hình, âm hiệu, lời** | **预演 (preview/pre-vis)**: 15 s ~6 credit so với ~180 credit bản thật Seedance 2.5 480p (<4%), thành viên có 15 s miễn phí; sửa prompt từng shot | Đa track, siêu phân giải cả bộ một nút; dịch vụ riêng cho TTS/ASR căn phụ đề | Tải truyện ≤100k chữ → tách thành **100 tập**; 60 tập có dàn ý + điểm treo mỗi tập; chạy lô tất cả asset | Giá/tiền chưa độc lập; yêu cầu **gắn nhãn AI**, bản quyền, an toàn nội dung, quyền hình ảnh người thật |

Mẫu hình chung: (a) tất cả dồn về "thư viện thành phần có tên + gọi bằng tham chiếu"; (b) kiểm duyệt ở khâu rẻ (khung tĩnh,
pre-vis) trước khi đốt credit video; (c) hãng nào cũng thừa nhận drift và tính phí mỗi lần thử → người dùng đau nhất ở **chi
phí lặp**, không ở chất lượng một lần.

## 2. Phát hiện đáng giá nhất

1. **Pre-vis rẻ trước khi sinh thật** (即梦: <4% chi phí; Higgsfield Popcorn ~$0.25/cảnh 8 khung; invideo: 25% clip dùng được).
   SagaReel đã có ảnh tĩnh qua gpt-image-2 — bản "animatic từ khung tĩnh + TTS" chính là pre-vis miễn phí để duyệt nhịp.
2. **Bộ ref tối giản thắng sheet phức tạp** (invideo: 1 mặt + 4 góc + chiều cao). Phù hợp quy tắc "ảnh mỏ neo" đã có và giới
   hạn 9 ảnh của gpt-image-2.
3. **Element/ngôn ngữ gọi `@tên`** (LTX, Kling, Higgsfield "image one"): shot tham chiếu thực thể có tên, không dán prompt tả
   lại. Khớp ý `{{ref N}}` của Toonflow.
4. **Trường tuỳ biến theo khung + snapshot** (Boords): shot list là bảng; chuyển đổi nguồn→bảng là snapshot, tránh ghi đè
   sửa tay. Cần quy tắc "đổi nguồn làm cũ cái gì" (đã có ý lineage).
5. **Ma trận ref theo trang phục × cảm xúc × bối cảnh** (即梦): thay vì một sheet/nhân vật, sinh các biến thể có tên (khớp `form`
   của aid-studio).
6. **Chuỗi mở rộng bằng "khung cuối làm ref kế"** (Higgsfield): quy trình tuyến tính chuẩn để đi dài mà không mất liên tục.
7. **Hậu duyệt có checklist cố định** (Flow community: hành động, máy, ánh sáng liền kề, tay/mặt/chữ/phản chiếu).
8. **Chi phí/hoàn tiền**: đối thủ tính phí mỗi lần thử; người dùng ghét "đốt credit". Hiển thị ước tính + tổng thực tế mỗi tập
   là khác biệt thật.
9. **Tuân thủ**: Trung Quốc buộc gắn nhãn AI, bản quyền nhạc/phông, quyền hình ảnh người thật — TikTok/YouTube cũng có cờ AI;
   cần trường "nhãn AI" trong gói đăng.
10. **Nhập truyện dài → chia N tập tự động, có "điểm treo" mỗi tập** (即梦) — đúng bài toán hàng trăm tập.

## 3. Điều thị trường làm kém (cơ hội cho SagaReel)

- Không công cụ nào chỉ ra rõ **nhất quán giọng nói + lời thoại tiếng Việt**; hầu hết giả định tiếng Anh/Trung.
- Không có **hệ thống phiên bản/đổi trường làm cũ phần nào** ở mức thương mại; người dùng phải sinh lại thủ công.
- Không có **gói xuất theo nền tảng** nhất quán (chương, bình luận ghim, ảnh bìa) ngoài vài editor.
- Canvas node (Flora/Spaces/Krea) rất tự do nhưng không có khái niệm tập/series và quản lý lô.

## 4. Áp dụng cho SagaReel

Ưu tiên: **must** (không có sản phẩm kém thuyết phục), **should** (giá trị cao, làm được trong mốc), **could** (nếu còn thời gian).

### M1 — Library / look / sheet / prompt compiler / drift check
| Mức | Khuyến nghị | Từ đâu |
|---|---|---|
| must | Thực thể có tên (nhân vật/địa điểm/đạo cụ) + **gọi bằng tham chiếu** trong shot; compiler tự sinh câu "ảnh N = vai trò" theo slot 9 ảnh | LTX/Kling Elements, Higgsfield |
| must | Bộ ref mặc định **tối giản**: 1 mặt chính diện (mỏ neo) + 4 góc + 1 ref chiều cao/dáng, cảnh báo khi sheet quá phức tạp (>9 ảnh hoặc ghép nhiều góc) | invideo audition-reel |
| must | **Ma trận biến thể** (trang phục × cảm xúc × bối cảnh) là look con có tên, sinh hàng loạt một nút nhưng có ước tính giá | 即梦 |
| should | Drift check bằng LLM vision: so ảnh mới với mỏ neo + checklist cố định (mặt, tóc, trang phục, màu, đạo cụ, tay/chữ) trả điểm + lý do | Flow checklist |
| should | Chế độ "cậy khung cuối" (last frame → ref của clip kế) trong compiler cho chuỗi dài | Higgsfield |
| could | Chỉnh nhanh biểu cảm/mặt bằng chỉ dẫn ngắn thay vì viết lại prompt | 即梦 "捏脸" |
| could | Gợi ý "thư viện diễn viên" mẫu ảnh chung | 即梦 7000+ diễn viên |

### M2 — Sequence / clip / take / review / import
| Mức | Khuyến nghị | Từ đâu |
|---|---|---|
| must | Clip có trường: cỡ cảnh, góc, chuyển động máy, ống kính, hướng nhìn, khung đầu/cuối, hành động, âm, lời, thời lượng; **trường tuỳ biến** theo template; mã ổn định | Boords, Kling Multi-Shot, 即梦 |
| must | **Bảng shot list + ảnh + trạng thái**, xuất CSV/XLSX; chuyển nguồn→bảng là snapshot, có diff khi nguồn đổi | Boords |
| must | Ưu tiên **pre-vis rẻ**: render animatic từ khung tĩnh + TTS + phụ đề thô; chỉ sau đó mới đốt credit Grok video | 即梦 预演 |
| must | Take lưu ảnh chụp đầu vào, trạng thái giữ/cắt/sửa/làm lại, hiển thị **tổng chi phí thực tế gồm lần thử lại** | Higgsfield complaints |
| should | Duyệt hàng loạt theo lưới (lọc: chưa duyệt, lệch nhân vật, lỗi tay/chữ) + phím tắt giữ/bỏ | thiếu ở thị trường |
| should | Sau 2 lần lỗi giống nhau gợi ý đổi cách (đổi ref/giảm hành động/rút ngắn) | zenstory |
| should | Import kịch bản (.fdx/Docs/txt/JSON `shot_id`) → clip nháp | Katalist |
| could | Xuất PDF storyboard cho khách duyệt | LTX, Katalist |

### M3 — Audio / voice / subtitles
| Mức | Khuyến nghị | Từ đâu |
|---|---|---|
| must | Giọng gán theo nhân vật (vi-VN) + sàn thời lượng theo lời; thuyết minh là đồng hồ | MoneyPrinter/huobao |
| must | Phụ đề từ word boundary TTS; kiểu phụ đề chuẩn dọc 9:16 (vùng an toàn, 2 dòng) | thị trường short drama |
| should | Chọn nghe "dùng âm gốc Grok vs thay TTS" mỗi clip (so sánh nhanh A/B) | — |
| should | Cờ **nhãn AI** + trường giấy phép nhạc/SFX | Trung Quốc compliance |
| could | Lipsync/nhận dạng lời cho đối chiếu phụ đề | Kling/Hedra |

### M4 — Studio timeline / render / export / platform presets
| Mức | Khuyến nghị | Từ đâu |
|---|---|---|
| must | Timeline đa track đơn giản (hình, thoại, nhạc, SFX, phụ đề) + render lại chỉ đoạn đã đổi | 即梦 đa track |
| must | Preset nền tảng (TikTok/Reels/Shorts 9:16, YouTube 16:9) + bộ đăng bài: tiêu đề, mô tả, bìa, nhãn AI | oss-study + compliance |
| should | Siêu phân giải/nâng cấp một nút cho cả tập (nếu provider có) | 即梦, Veo |
| could | Xuất EDL/XML cho Premiere/CapCut | Katalist |

### M5 — AI writing / story memory / workflows
| Mức | Khuyến nghị | Từ đâu |
|---|---|---|
| must | Nhập truyện dài → tách N tập tự động, dàn ý tập có **điểm treo** cuối tập | 即梦 (100 tập, 60 tập) |
| must | Template thể loại + phong cách ("skill") sửa được; nhân vật có "nhãn nhân thiết" chống trôi tính cách khi LLM viết | 即梦 |
| should | Chạy lô một tập đầu ("thử 30 s") trước khi nhân ra hàng loạt | Tencent pipeline |
| should | Hạt giống tất định theo `shot_id` (băm ổn định, không dùng `hash()` ngẫu nhiên) cho cache/lặp lại | Tencent sample bug |
| could | Prompt → workflow (như Krea) để tạo template từ mô tả | Krea |

### M6 — Channel analysis / clone from video
| Mức | Khuyến nghị | Từ đâu |
|---|---|---|
| should | Phân tích video mẫu → shot list + nhịp cắt + hook, không sao chép nội dung | — (thị trường chưa có tốt) |
| could | Gợi ý biến thể theo hiệu suất kênh | — |

### M7 — MCP / agents
| Mức | Khuyến nghị | Từ đâu |
|---|---|---|
| should | Tool "ước lượng chi phí lô" + "trạng thái tập" trước khi agent chạy; agent không được bỏ qua gate | Higgsfield/Kinema |
| could | Agent sửa nhân thiết theo drift báo cáo | invideo Agent One |

### M8 — Polish
| Mức | Khuyến nghị | Từ đâu |
|---|---|---|
| should | Màn hình "tổng chi phí tập" và cảnh báo credit (đã quan trọng với MaiVideo ~60 ảnh) | tất cả |
| could | Giao diện lưới node tuỳ chọn để xem phụ thuộc (không làm trung tâm) | Flora/Spaces |

## 5. Nguồn (truy cập 2026-11-20)

- LTX: https://ltx.studio/blog/ltx-storyboard-generator-update ; https://ltx.io/blog/how-to-make-character-ai ; https://ltx.studio/blog/getting-started-with-elements
- Higgsfield: https://higgsfield.ai/blog/The-AI-Storyboard-Generator-That-Feels-Like-Directing ; https://higgsfield.ai/blog/ai-short-film-pipeline ; https://higgsfield.ai/blog/why-ai-video-credits-run-out
- OpenArt: https://openart.ai/blog/consistent-characters-in-ai-video/ ; https://www.producthunt.com/p/openart/openart-consistent-characters
- Katalist: https://katalist.ai/ai-storyboard-generator ; https://www.katalist.ai/agentic-storyboarder
- Runway: https://help.runwayml.com/hc/en-us/sections/39888423025683-Gen-4
- Kling: https://kling.ai/blog/kling-3-subject-binding-character-consistency ; https://kling.ai/blog/kling-video-3-director-mode-multi-shot-tutorial
- Google: https://blog.google/innovation-and-ai/technology/ai/veo-3-1-ingredients-to-video/ ; https://labs.google/fx/tools/flow/faq
- Invideo: https://invideo.io/academy/i-tried-every-ai-character-consistency-workflow-results/ ; https://invideo.io/faq/how-do-you-reduce-ai-video-generation-waste-and-get-more/
- Krea/Flora/Spaces: https://www.krea.ai/blog/freepik-spaces-vs-krea-nodes
- Boords: https://boords.com/shot-list-software ; https://boords.com/iterate/custom-note-fields
- CapCut/Dreamina: https://dreamina.capcut.com/ai-video/ai-short-drama ; https://www.capcut.com/ideas/seedance-2-0-for-script-to-video
- AI漫剧: https://ai-bot.cn/jimeng-ai-drama-studio-review/ ; https://cloud.tencent.com/developer/article/2752882 ; https://www.csdn.net/article/2026-08-26/164090798
