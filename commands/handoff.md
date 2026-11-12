---
description: Bàn giao sang phiên mới — soạn bản tóm tắt ≤ 2k token, lưu file, hỏi rồi mở phiên mới kèm bản tóm tắt
argument-hint: "[chủ đề]"
---

Cách chạy công cụ (gọi là `CTT` bên dưới): trên Windows `py -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`, nơi khác `python3 -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`. Nếu không có Python ≥ 3.11 thì công cụ tự báo cách cài — chuyển nguyên thông báo đó cho người dùng và dừng. Nếu lệnh báo không tìm thấy file `bin/ctt.py`, hoặc đường dẫn còn chữ CLAUDE_PLUGIN_ROOT chưa được thay, báo người dùng là plugin cài chưa đúng và dừng. Đường dẫn là một thư mục bình thường thì cứ chạy.

Mục đích: phiên này đã dài, mỗi lần gọi tool đọc lại toàn bộ context. Bản bàn giao gói những gì phiên mới cần biết vào ≤ 2k token để làm tiếp ở phiên gọn. Không làm tiếp task ở đây, không commit/push.

## 1. Lấy sự thật từ repo

Chạy `CTT handoff-facts --cwd "<thư mục làm việc hiện tại>"`. Kết quả JSON có: nhánh, 5 commit cuối, file đã stage / chưa stage / chưa track, remote, thư mục lưu (`handoffs_dir`), giới hạn token và **mẫu** (`template`). Ngoài repo git thì `repo` là null — bỏ phần git.

## 2. Soạn bản bàn giao theo mẫu

Viết từ chính context phiên này (không đọc lại transcript, không mở thêm file chỉ để tóm tắt). Đủ 4 mục của mẫu, đúng tên tiêu đề:

- **Trạng thái**: repo, nhánh, commit cuối, thay đổi chưa commit (tóm tắt theo nhóm, không liệt kê từng file nếu nhiều), việc vừa xong, test nào pass (con số thật đã chạy).
- **Quyết định đã chốt**: điều đã chọn và lý do, cái đã thử mà không được, cái bẫy đã gặp — những thứ phiên mới dễ làm lại sai. Chỉ ghi điều đã kiểm chứng; điều chưa chắc thì ghi rõ "giả thuyết".
- **Việc cần làm tiếp**: đánh số theo thứ tự, mỗi việc đủ rõ để bắt tay làm (đầu vào, xong khi nào). Ghi cả quy tắc người dùng đã đặt cho phiên (vd. hỏi trước khi commit).
- **File liên quan**: đường dẫn, ngắn.

Không đưa mật khẩu, token, khoá API hay dữ liệu cá nhân vào bản bàn giao. Chủ đề: `$ARGUMENTS` nếu có, nếu không tự đặt 2–4 từ (vd. `token-tools handoff`).

## 3. Lưu

Ghi bản nháp ra một file tạm (thư mục scratchpad nếu có, nếu không là `<handoffs_dir>/.draft.md`), rồi chạy `CTT handoff-save --topic "<chủ đề>" --file "<file nháp>"`. Nếu lệnh báo quá giới hạn token hoặc thiếu mục: sửa nháp (rút gọn, bỏ chi tiết phiên mới tự tìm được) và chạy lại. Lưu xong thì xoá file nháp. Lệnh không bao giờ ghi đè (trùng tên thì thêm `-2`).

## 4. Hỏi rồi mới mở phiên mới

Báo người dùng: đường dẫn file, số token, và nội dung bản bàn giao. Rồi hỏi (dùng `AskUserQuestion` nếu có) có mở phiên mới kèm bản bàn giao không — lựa chọn: "Mở phiên mới" / "Chỉ lưu file". Không mở khi chưa có câu trả lời "có".

Nếu người dùng đồng ý:
- **Trong app desktop Claude Code** (có tool `mcp__ccd_session__start_session`; nếu nó chỉ có tên trong danh sách deferred thì nạp bằng `ToolSearch` query `select:mcp__ccd_session__start_session`): gọi nó với `initiation` "user_asked", `context` "fresh", `title` là chủ đề, và prompt:
  `Đọc bản bàn giao <đường dẫn file> trước tiên — đó là toàn bộ ngữ cảnh (không đọc lại transcript phiên cũ). Rồi làm theo mục "Việc cần làm tiếp".`
- **Ngoài app**: in một lệnh để người dùng tự chạy trong terminal mới, trong một khối ```bash riêng:
  `claude "Đọc bản bàn giao <đường dẫn file> trước tiên — đó là toàn bộ ngữ cảnh. Rồi làm theo mục \"Việc cần làm tiếp\"."`

Cuối cùng nói một dòng: phiên này có thể dừng hoặc lưu trữ; phiên mới bắt đầu với ~2k token thay vì toàn bộ context hiện tại.
