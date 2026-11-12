---
description: Khai các thư mục chứa project trên máy cho token-tools
argument-hint: "[thư mục ...]"
---

Cách chạy công cụ (gọi là `CTT` bên dưới): trên Windows `py -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`, nơi khác `python3 -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`. Nếu không có Python ≥ 3.11 thì công cụ tự báo cách cài — chuyển nguyên thông báo đó cho người dùng và dừng. Nếu lệnh báo không tìm thấy file `bin/ctt.py`, hoặc đường dẫn còn chữ CLAUDE_PLUGIN_ROOT chưa được thay, báo người dùng là plugin cài chưa đúng và dừng. Đường dẫn là một thư mục bình thường thì cứ chạy.

1. Nếu `$ARGUMENTS` có thư mục, dùng chúng. Nếu không, gợi ý các thư mục có thật trên máy chứa nhiều project (ví dụ `~/IdeaProjects`, `~/projects`, `~/source/repos`, `~/code`) và hỏi người dùng chọn — không tự quyết.
2. Chạy `CTT init --root <thư mục>` (lặp `--root` cho từng thư mục). Lệnh này ghi `~/.claude/claude-token-tools/policy.toml`; bản cập nhật plugin không ghi đè file đó.
3. Chạy `CTT sync` (dry run) để người dùng thấy các agent sẽ được áp model, và gợi ý `/token-tools:sync-models` nếu muốn ghi.
