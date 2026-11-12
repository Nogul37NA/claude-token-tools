---
description: Áp chính sách model/effort cho agent của mọi project và model phiên chính (xem diff trước khi ghi)
argument-hint: "[--root THƯ_MỤC]"
---

Cách chạy công cụ (gọi là `CTT` bên dưới): trên Windows `py -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`, nơi khác `python3 -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`. Nếu không có Python ≥ 3.11 thì công cụ tự báo cách cài — chuyển nguyên thông báo đó cho người dùng và dừng. Nếu lệnh báo không tìm thấy file `bin/ctt.py`, hoặc đường dẫn còn chữ CLAUDE_PLUGIN_ROOT chưa được thay, báo người dùng là plugin cài chưa đúng và dừng. Đường dẫn là một thư mục bình thường thì cứ chạy.

1. Chạy `CTT sync $ARGUMENTS` (dry run, chưa ghi gì). Nếu không có thư mục project nào được khai, công cụ chỉ xét thư mục hiện tại — gợi ý người dùng chạy `/token-tools:token-setup` trước.
2. Cho người dùng xem diff (tóm theo từng file: agent nào đổi model/effort gì, settings.json nào đổi model). Nói rõ chính sách lấy từ `defaults/policy.toml` của plugin, ghi đè bằng `~/.claude/claude-token-tools/policy.toml`.
3. Hỏi người dùng có ghi không. **Chỉ khi người dùng đồng ý rõ ràng** mới chạy lại với `--apply`. Không tự sửa file agent hay settings bằng tay.
4. Sau khi ghi: nhắc model mới có hiệu lực từ phiên Claude Code tiếp theo.
