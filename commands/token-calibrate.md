---
description: Bảng P50/P90 token và chi phí theo loại task (dùng để ước lượng trước khi làm)
argument-hint: "[số ngày, mặc định 30]"
---

Cách chạy công cụ (gọi là `CTT` bên dưới): trên Windows `py -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`, nơi khác `python3 -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`. Nếu không có Python ≥ 3.11 thì công cụ tự báo cách cài — chuyển nguyên thông báo đó cho người dùng và dừng. Nếu lệnh báo không tìm thấy file `bin/ctt.py`, hoặc đường dẫn còn chữ CLAUDE_PLUGIN_ROOT chưa được thay, báo người dùng là plugin cài chưa đúng và dừng. Đường dẫn là một thư mục bình thường thì cứ chạy.

Chạy `CTT calibrate --days N`, với N là `$ARGUMENTS` nếu đó là một số, mặc định 30.

Giữ nguyên bảng theo loại task, rồi nêu tối đa 3 nhận xét có số liệu từ chính kết quả (ví dụ chênh lệch P50/P90, phiên nhiều ngày so với trong 1 ngày, phiên tốn nhất). Nếu công cụ báo có model không có trong bảng giá, nhắc người dùng thêm vào `~/.claude/claude-token-tools/prices.toml`. Loại task được gán sau khi làm (theo số file nguồn đã sửa), nói rõ điều này.
