---
description: Ghi mốc % hạn mức gói (5 giờ / tuần) và quy đổi ra $ / 1%
argument-hint: "[--five-hour P --five-hour-reset ISO --week P --week-reset ISO]"
---

Cách chạy công cụ (gọi là `CTT` bên dưới): trên Windows `py -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`, nơi khác `python3 -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`. Nếu không có Python ≥ 3.11 thì công cụ tự báo cách cài — chuyển nguyên thông báo đó cho người dùng và dừng. Nếu lệnh báo không tìm thấy file `bin/ctt.py`, hoặc đường dẫn còn chữ CLAUDE_PLUGIN_ROOT chưa được thay, báo người dùng là plugin cài chưa đúng và dừng. Đường dẫn là một thư mục bình thường thì cứ chạy.

1. Lấy % hạn mức:
   - Nếu `$ARGUMENTS` đã có các tham số `--five-hour` / `--week`, dùng nguyên.
   - Nếu không, và có tool `mcp__ccd_session_mgmt__get_usage` (app desktop), gọi nó: lấy `percentUsed` và `resetsAt` của cửa sổ "5-hour limit" và "Weekly · all models".
   - Nếu không có cả hai, hỏi người dùng % đã dùng và thời điểm reset (xem ở màn Usage / `/usage`). Nếu `plan.status` là `not_applicable` (dùng API key) thì nói hạn mức gói không áp dụng và dừng.
2. Chạy `CTT limits --five-hour P --five-hour-reset ISO --week P --week-reset ISO`.
3. Báo lại: mốc vừa ghi, trung vị $ / 1% của từng cửa sổ và số mốc đã có. Nhắc đây là cận dưới (dùng claude.ai hay máy khác cũng tính vào hạn mức) và cần khoảng 5 mốc ở các thời điểm khác nhau mới ổn định.
