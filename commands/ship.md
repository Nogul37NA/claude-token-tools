---
description: Quy trình chuẩn 1 yêu cầu → sản phẩm — hỏi gọn, ước lượng, chia việc theo vai (subagent / phiên con), chạy, kiểm chứng, giao; chỉ 2 lần duyệt
argument-hint: "<yêu cầu | mã Jira | file SRS | link wiki/Figma …>"
---

Cách chạy công cụ (gọi là `CTT` bên dưới): trên Windows `py -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`, nơi khác `python3 -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`. Nếu không có Python ≥ 3.11 thì công cụ tự báo cách cài — chuyển nguyên thông báo đó cho người dùng và dừng. Nếu lệnh báo không tìm thấy file `bin/ctt.py`, hoặc đường dẫn còn chữ CLAUDE_PLUGIN_ROOT chưa được thay, báo người dùng là plugin cài chưa đúng và dừng.

Yêu cầu: `$ARGUMENTS`. Gõ lệnh này là người dùng **đã yêu cầu rõ** được chia việc ra subagent và phiên con theo bảng chia việc họ duyệt — không hỏi lại từng lần mở. Người dùng chỉ duyệt **2 lần**: (1) phiếu + bảng chia việc, (2) merge/commit. Ngoài 2 lần đó chỉ dừng khi bị chặn thật (thiếu quyền, ngân sách chạm 100%, câu hỏi chỉ người dùng trả lời được).

Mỗi khi sang bước mới, chạy `CTT ship-stage <id> --stage <n>` (Phòng điều hành hiện tiến độ từ đó). Mỗi lệnh `CTT ship-*` chạy **riêng một lần gọi tool**, không nối với lệnh khác (`;`, `|`, `&&`): khung ghi lỗi theo cả lần gọi, nối lệnh thì lỗi của lệnh kia hiện thành lỗi quy trình. Các tham số đã ghi đủ ở đây, không cần chạy `--help`. Giữ ngắn mọi tin nhắn: người dùng theo dõi trên khung, không đọc dài.

## 0. Nhận yêu cầu

0. Người dùng nói "chạy tiếp", "làm tiếp lượt dở" hoặc đưa một mã lượt: không mở lượt mới. `CTT ship-resume` (không tham số) liệt kê các lượt dở; `CTT ship-resume <id>` gắn lượt vào phiên này và in từng bước đang ở đâu, bước nào chạy được ngay. Làm tiếp từ đúng bước quy trình nó in ra; bước đang ghi `running` của phiên trước thì xem kết quả đã có (file, nhánh) rồi mới chạy lại.
1. Chốt **thư mục làm việc** (nơi sản phẩm được tạo). Việc mới, độc lập thì đề xuất một thư mục riêng thay vì tạo trong repo đang mở. `CTT ship-start --title "<tên ngắn 3–8 từ>" --cwd "<thư mục làm việc>"`. Đổi thư mục sau đó thì chạy `CTT ship-stage <id> --stage <n> --cwd "<thư mục mới>"` ngay: quyền theo vai của agent chỉ cho ghi trong thư mục phiên và thư mục này (không khai thì tester/developer bị chặn ghi). Ghi lại `run.id` và `workflow` (`clarify_max`, `max_steps`, `fix_rounds`, `session_min_tokens`, `auto_approve_usd`).
2. Đọc lướt đầu vào như `/token-tools:token-estimate` bước 1 (cùng trần token từng nguồn, không bắt đầu làm).
3. Chỉ hỏi câu **chặn việc** (không đoán được, đoán sai thì làm lại nhiều): tối đa `clarify_max` câu, gom một lần (`AskUserQuestion` nếu có, mỗi câu có lựa chọn đề xuất đứng đầu). Không chặn thì tự chọn mặc định hợp lý và ghi vào giả định.
4. Chốt **tiêu chí xong**: 3–7 dòng kiểm được (lệnh chạy được, test pass, màn hình hiện X, API trả Y). Đây là thước đo ở bước 4.

## 1. Ước lượng

`CTT ship-stage <id> --stage 1`. Làm phiếu như `/token-tools:token-estimate` bước 2 và 4 (bỏ bước 3 xác nhận hồ sơ — gộp vào lần duyệt ở bước 2 dưới đây). Ghi lại mã phiếu và `usd.budget`.

## 2. Chia việc — duyệt lần 1

`CTT ship-stage <id> --stage 2`. Dùng `token-tools:planner` (subagent, chỉ đọc) nếu repo lạ hoặc việc > 3 bước; việc nhỏ thì tự chia. Mỗi bước một dòng:

| Bước | Vai | Cách chạy | Chờ bước | File / vùng | Xong khi | Ước token |
|---|---|---|---|---|---|---|

Quy tắc **cách chạy** (đây là chỗ quyết định chi phí):
- `main` — phiên chính tự làm: việc ≤ vài lượt gọi tool, hoặc cần context đang có.
- `subagent` — tra cứu, review, viết/chạy test, việc ngắn một vai: `token-tools:<vai>` (model theo `[roles]` của policy). Mặc định chọn cái này.
- `session` — phiên con: **chỉ** khi bước lớn (ước ≥ `session_min_tokens`), độc lập (không cần kết quả bước khác, không đụng file của bước khác), sống lâu hoặc người dùng sẽ muốn mở ra xem riêng. Phiên con ghi code thì có worktree riêng.
- Không quá `max_steps` dòng; bước kiểm chứng cuối (test + review) luôn có.
- **Chờ bước** (`needs`): tên các bước phải xong trước (bước cần kết quả hoặc đụng file của bước kia). Để trống = chạy song song được. Mỗi bước một tên riêng; chờ nhau thành vòng thì lệnh từ chối.

Ghi bảng ra file JSON trong scratchpad: `{"steps": [{"name", "role", "mode": "main|subagent|session", "needs": [], "files": [], "done": "…", "tokens": 0}]}`.

Trình người dùng: tiêu chí xong, giả định, phiếu (nguyên bảng công cụ in), bảng chia việc. Hỏi một câu: duyệt ngân sách và kế hoạch? Kế hoạch có bước `session` thì câu hỏi và lựa chọn đề xuất phải **nêu đích danh** việc mở phiên con, ví dụ lựa chọn "Duyệt, mở 2 phiên con" — chế độ Auto chỉ cho mở phiên khi lời người dùng nói rõ điều đó (lần thử 12/11/2026: chọn "Duyệt" chung chung → "Blocked by auto mode"). Nếu `auto_approve_usd` > 0 và `usd.budget` ≤ số đó: không hỏi, nói một dòng "tự duyệt vì phiếu ≤ $X".

Khi đã duyệt: `CTT estimate-approve <mã phiếu>` rồi `CTT ship-stage <id> --stage 3 --estimate <mã phiếu> --plan <file JSON>`. Lệnh từ chối kế hoạch vượt trần (`max_steps`, phiên con dưới ngưỡng, quá `[sessions] max_children`) — sửa bảng rồi chạy lại, không cần hỏi lại người dùng nếu chỉ đổi `session` → `subagent`.

## 3. Chạy

Chọn bước: `CTT ship-next <id>` in các bước chạy được ngay (mọi bước nó chờ đã xong); chạy hết các bước đó song song, xong một bước thì gọi lại để lấy đợt kế. Lệnh từ chối cho chạy bước còn đang chờ.

Mỗi bước:
- Bắt đầu: `CTT ship-step <id> --name "<bước>" --status running` → in **mã lần thử** (vd `a1-3f9c`). Ghi mã đó vào lời giao việc và yêu cầu agent / phiên con ghi lại nó ở dòng đầu báo cáo.
- Kết thúc: `CTT ship-step <id> --name "<bước>" --status done|failed --attempt <mã> [--file <đường dẫn> …] [--tests "<kết quả test, một dòng>"] [--note "…"]`. `--file` và `--tests` lấy từ báo cáo của agent; bước kiểm chứng và báo cáo ship đọc lại chúng.
- Lệnh báo mã là của lần thử cũ: đó là báo cáo đến muộn của một agent đã bị thay — bỏ qua báo cáo đó, không sửa mã cho khớp.
- Chạy lại một bước lỗi: `--status running` lần nữa → mã mới, mã cũ hết hiệu lực. Bước lỗi 2 lần thì dừng bước đó, ghi thành việc mở (các bước chờ nó cũng dừng).

- Bước độc lập chạy song song: subagent chạy nền; phiên con bằng `mcp__ccd_session__start_session` (`initiation` "user_asked", `context` "fresh", `use_worktree` true nếu ghi code, **không** đặt `permission_mode` để giữ cùng chế độ). Lời giao việc gồm: mục tiêu bước, file/vùng, tiêu chí xong của bước, ràng buộc (không commit nếu người dùng chưa cho, không push), mã lần thử, và "báo cáo ≤ 10 dòng, dòng đầu là mã lần thử: đã làm gì, file nào, test nào, việc còn mở".
- `cwd` của `start_session` viết đúng như hệ điều hành viết (Windows: `C:\Users\…`, gạch ngược) — viết khác bị từ chối. Thư mục chưa được tin cậy thì app hiện nút *Trust repository*: nói người dùng bấm, rồi gọi lại đúng lệnh đó một lần. Không tự thêm dấu gì vào lời giao việc: plugin tự nối phiên con với phiên này.
- `start_session` bị từ chối (vd "Blocked by auto mode", hết chỗ): không thử lại quá 1 lần, không dừng cả lượt — chạy bước đó bằng subagent đúng vai (chạy nền), `CTT ship-step <id> --name "<bước>" --status running --note "phiên con bị chặn → subagent"`, và nói một dòng cho người dùng: muốn phiên con thật thì lần sau trả lời bằng lời có "mở phiên con".
- **Ngay sau khi mỗi phiên con khởi động** (kết quả `start_session` có `session_id`): mở nó cạnh phiên này để người dùng quan sát — `mcp__ccd_window__open_session_in` với `session_id` đó và `target` "split" (tool chỉ có tên trong danh sách deferred thì nạp bằng `ToolSearch` query `select:mcp__ccd_window__open_session_in`). Phiên con tự nằm dưới phiên này trên thanh bên. Tool báo không làm được (người dùng đang xem phiên khác, hết chỗ chia khung) thì không thử lại, nói một dòng: "Phiên con <tên> đang chạy, mở từ thanh bên (nằm dưới phiên này)". Ngoài app desktop (không có tool này) thì bỏ qua.
- Cần hỏi lại một agent/phiên con: `SendMessage` tới agentId / phiên đó, ngắn. Plugin tự gắn mã `[q:…]`; trả lời mở đầu bằng `[re:…]`.
- Không đọc transcript của phiên con hay agent; chỉ dùng báo cáo.
- Ngân sách: Phòng điều hành báo ở 50/80/100%. Ở 100% dừng bước đang chạy và hỏi người dùng (đây là chặn thật).

## 4. Kiểm chứng

`CTT ship-stage <id> --stage 4`. Phiên con ở worktree riêng: báo nhánh của từng phiên (`refs/heads/…`) và hỏi người dùng có gộp vào đây không (chỉ gộp khi họ đồng ý). Rồi:
0. `CTT ship-resume <id>` in lại từng bước kèm file đã đổi và kết quả test đã ghi — giao danh sách file đó cho tester và reviewer thay vì để họ tự dò.
1. `token-tools:tester`: chạy build + test liên quan, báo pass/fail bằng con số.
2. `token-tools:reviewer`: review diff theo quy ước project.
3. Đối chiếu từng dòng tiêu chí xong: ✓ / ✗ kèm bằng chứng (lệnh, kết quả).

Có ✗ hoặc lỗi review mức nặng: sửa (subagent đúng vai) rồi kiểm lại — tối đa `fix_rounds` vòng; còn lại ghi thành việc mở.

## 5. Giao — duyệt lần 2

`CTT ship-stage <id> --stage 5`. Báo cáo ngắn:
- Đã làm gì (theo bước), file chính, cách chạy/thử.
- Tiêu chí xong: bảng ✓/✗.
- Chi phí: `CTT estimate-check --id <mã phiếu>` (thực tế so với P50/P90).
- Việc còn mở.

Hỏi một câu: commit (và merge nhánh phiên con nếu có)? Chỉ làm khi người dùng đồng ý; không push trừ khi họ nói.

## 6. Học

`CTT ship-finish <id> --result ok|partial|failed --note "<1 dòng: điều lần sau nên làm khác>"`. Lệnh ghi thực tế so với phiếu vào lượt — dữ liệu cho `/token-tools:token-calibrate` và `ctt delegation` lần sau. Nói một dòng kết quả cho người dùng.
