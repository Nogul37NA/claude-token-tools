---
description: Phiếu ước lượng token, chi phí, % hạn mức, model và rủi ro trước khi làm một task (mô tả, Jira, SRS, wiki, Figma, mockup)
argument-hint: "<mô tả | mã Jira | đường dẫn file | link wiki/Figma/mockup …> [--yes]"
---

Cách chạy công cụ (gọi là `CTT` bên dưới): trên Windows `py -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`, nơi khác `python3 -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`. Nếu không có Python ≥ 3.11 thì công cụ tự báo cách cài — chuyển nguyên thông báo đó cho người dùng và dừng. Nếu lệnh báo không tìm thấy file `bin/ctt.py`, hoặc đường dẫn còn chữ CLAUDE_PLUGIN_ROOT chưa được thay, báo người dùng là plugin cài chưa đúng và dừng. Đường dẫn là một thư mục bình thường thì cứ chạy.

Đầu vào: `$ARGUMENTS`. Mục tiêu là **đếm** để ước lượng, không phải hiểu hết. Tổng token đọc cho bước này không quá 30k. Không chụp ảnh, không đọc chi tiết, không bắt đầu làm task.

## 1. Đọc lướt từng nguồn (trần token mỗi nguồn trong ngoặc)

- **Mô tả tự do**: dùng nguyên văn.
- **Mã Jira** (8k): chỉ tiêu đề, mô tả, AC, loại issue, 5 comment gần nhất, danh sách subtask. Dò công cụ theo thứ tự: tool MCP Jira nếu có → CLI `.mcp` trong repo hiện tại (vd `node .mcp/index.js jira …`) nếu có → trình duyệt tích hợp (đọc text trang; nếu gặp trang đăng nhập, nhờ người dùng đăng nhập rồi đọc tiếp).
- **File local** (12k): `.md` đọc mục lục + tiêu đề cấp 1–2 + bảng UC/màn hình; `.pdf` chỉ trang mục lục và 2 trang đầu mỗi chương (Read theo `pages`); `.docx`/`.xlsx` lấy tiêu đề và tên sheet/bảng.
- **Link wiki** (10k): cây trang con, tiêu đề, bảng. Dò: MCP wiki → trình duyệt tích hợp. Không đọc lịch sử trang.
- **Link Figma** (4k): chỉ metadata — danh sách frame và tên (tool Figma `get_metadata` nếu có), không chụp ảnh.
- **Mockup HTML** (4k): mở trong trình duyệt tích hợp, đếm view/tab/modal/form/bảng qua DOM, không chụp.
- **Ảnh** (4k): tối đa 2 ảnh.
- **Code hiện có** (2k): nếu có tool code-graph thì `cg_impact` cho class chính; không thì Grep tên class để đếm nơi dùng.

Nguồn nào vượt trần: dừng đọc nguồn đó, đánh dấu `"complete": false`.

## 2. Dựng hồ sơ task (JSON)

Ghi file `estimate-profile.json` vào thư mục tạm của phiên (scratchpad nếu có), theo đúng cấu trúc:

```json
{"title": "…", "kind": "qa | investigation | change_existing | new_feature | product",
 "sources": [{"type": "text|jira|file|wiki|figma|html_mockup|image|code", "ref": "…", "tokens_read": 0, "complete": true}],
 "scope": {"screens": 0, "apis": 0, "entities": 0, "migrations": 0, "integrations": 0, "impacted_classes": 0},
 "features": [{"name": "…", "kind": "change_existing|new_feature", "scope": {…}, "risks": []}],
 "risks": ["…"], "unknowns": ["câu hỏi còn mở"], "repo": "…", "branch": "…"}
```

- `kind = product` khi đầu vào là cả sản phẩm / nhiều tính năng: mỗi tính năng một mục `features` với scope riêng.
- `risks` chỉ dùng mã trong `${CLAUDE_PLUGIN_ROOT}/defaults/risks.toml` (đọc file đó để biết mã). Rủi ro theo nhánh, migration, tích hợp, vùng ảnh hưởng rộng, ≥ 3 câu hỏi mở được công cụ tự thêm — không cần ghi.
- `branch`: nhánh git hiện tại của repo (nếu có).
- Chỉ ghi `files_estimate` khi người dùng nói rõ số file.

## 3. Xác nhận hồ sơ (bắt buộc, trừ khi có `--yes`)

Cho người dùng xem bảng ngắn: loại task, scope (từng số), rủi ro, câu hỏi mở, nguồn nào đọc chưa hết. Hỏi có sửa gì không. Sửa theo lời người dùng rồi mới sang bước 4.

## 4. Tính phiếu

Nếu có tool `mcp__ccd_session_mgmt__get_usage`, gọi để lấy `percentUsed` của "Weekly · all models" (dùng cho `--week-used`; nếu chưa có mốc nào gần đây thì nhân tiện chạy `CTT limits` với số đó như `/token-tools:token-limits`).

Chạy `CTT estimate --profile <file> --cwd "<thư mục repo hiện tại>" [--week-used P]`.

Trình bày nguyên bảng phiếu công cụ in ra (không tự đổi số), rồi mở trang HTML phiếu cho người dùng xem (gửi file với chế độ hiển thị nếu có tool gửi file; không thì đưa đường dẫn). Nói rõ: chi phí là giá API quy đổi; % hạn mức là cận dưới.

## 5. Duyệt ngân sách

Hỏi người dùng có duyệt ngân sách không. **Chỉ khi người dùng đồng ý rõ ràng** mới chạy `CTT estimate-approve <id>` — từ đó hook sẽ báo khi phiên này dùng tới 50%, 80%, 100% ngân sách. Không bắt đầu làm task trong lệnh này.
