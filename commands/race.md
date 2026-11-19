---
description: Đua nhiều agent — cùng một việc giao 2–3 subagent khác model/effort, mỗi agent một worktree; test + review mù chấm điểm, bảng so sánh kèm $, bạn chọn bản thắng
argument-hint: "<việc cần làm> [--entrants sonnet:medium,opus:high]"
---

Cách chạy công cụ (gọi là `CTT` bên dưới): trên Windows `py -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`, nơi khác `python3 -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`. Nếu không có Python ≥ 3.11 thì công cụ tự báo cách cài — chuyển nguyên thông báo đó cho người dùng và dừng. Nếu lệnh báo không tìm thấy file `bin/ctt.py`, hoặc đường dẫn còn chữ CLAUDE_PLUGIN_ROOT chưa được thay, báo người dùng là plugin cài chưa đúng và dừng.

Yêu cầu: `$ARGUMENTS`. Gõ lệnh này là người dùng **đã yêu cầu rõ** được chạy song song các subagent đối thủ với model/effort ghi trong lượt đua (kể cả `model` và `effort` của từng lần gọi Agent). Người dùng chỉ được hỏi **1 lần**: chọn bản thắng (và có merge không). Mỗi lệnh `CTT race-*` chạy **riêng một lần gọi tool**, không nối với lệnh khác, và **tuần tự** (không gửi hai lệnh `race-*` song song trong cùng một tin nhắn — chúng ghi chung một file). Giữ ngắn mọi tin nhắn: người dùng theo dõi trên Phòng điều hành.

## 1. Mở lượt đua

1. Tách việc cần làm khỏi `--entrants` (nếu có). Việc phải tự đủ để một agent không có context làm được: mục tiêu, file/vùng, tiêu chí xong kiểm được (lệnh test). Thiếu tiêu chí kiểm được thì tự viết 2–4 dòng từ yêu cầu và repo; chỉ hỏi khi không đoán được.
2. Working tree phải sạch (worktree tạo từ commit hiện tại; thay đổi chưa commit không có trong worktree). `race-start` tự từ chối khi tree bẩn hoặc thư mục không phải git: báo nguyên lý do một dòng và dừng — không tự stash/commit.
3. `CTT race-start --title "<tên 3–8 từ>" --task "<việc, một đoạn>" --cwd "<repo>" [--entrants "<danh sách>"]`. Ghi lại `race.id` và các đối thủ (A, B, C với model, effort).

## 2. Chạy các đối thủ (song song)

Với mỗi đối thủ, trong **cùng một tin nhắn**, gọi Agent: `subagent_type` "token-tools:developer", `model` và `effort` của đối thủ, `isolation` "worktree", `run_in_background` true, `description` "Race <id> · <tên>". Lời giao việc giống hệt nhau cho mọi đối thủ (chỉ khác nhau model/effort): việc + tiêu chí xong, "làm trong worktree này, commit kết quả vào nhánh của worktree, không push, không đụng thư mục khác", và "báo cáo ≤ 6 dòng: đã làm gì, lệnh test và kết quả, nhánh, đường dẫn worktree".

Sau mỗi lần gọi: `CTT race-entry <id> --name <tên> --agent-id <agentId> --status running`. Khi đối thủ báo xong: `CTT race-entry <id> --name <tên> --worktree "<đường dẫn>" --branch "<nhánh>" --status done` (lỗi / không làm được: `--status failed --note "<1 dòng>"`). Không đọc transcript của đối thủ.

## 3. Chấm điểm

Khi mọi đối thủ đã xong hoặc lỗi:
1. **Test:** một `token-tools:tester` (chạy nền) chạy cùng một lệnh test trong từng worktree, báo số pass/fail từng worktree. Rồi `CTT race-score <id> --name <tên> --tests-pass N --tests-fail M` cho từng đối thủ.
2. **Review mù:** tự lấy diff của từng bản bằng `git -C <worktree> diff <base>` (gồm cả phần chưa commit), xáo thứ tự và đặt nhãn mới X/Y/Z (ghi nhớ nhãn ↔ tên), rồi đưa **nội dung diff** cho một `token-tools:reviewer` — không đưa đường dẫn worktree, tên nhánh, model, effort, chi phí hay lịch sử commit (dòng `Co-Authored-By` lộ model). Yêu cầu chấm mỗi bản 0–10 theo: đúng tiêu chí xong, đúng quy ước repo, gọn, rủi ro; trả về điểm + 1 dòng lý do mỗi bản. Đổi nhãn X/Y/Z về tên đối thủ rồi `CTT race-score <id> --name <tên> --review <điểm> --notes "<lý do>"`.
3. `CTT race-report <id>` — trình nguyên bảng công cụ in (không tự đổi số) và dòng đề xuất.

## 4. Chọn bản thắng — hỏi người dùng 1 lần

Hỏi một câu, lựa chọn đầu là bản đề xuất: "Merge bản <tên> (<model·effort>, <điểm>, $<chi phí>)", các bản khác, và "Không merge". Khi người dùng chọn:
- `CTT race-pick <id> --winner <tên> --note "<1 dòng>"` (kể cả khi không merge: ghi bản họ thấy tốt nhất; "không bản nào đạt" thì `CTT race-cancel <id> --note "…"`).
- Merge chỉ khi người dùng chọn merge: `git merge --no-ff <nhánh thắng>` vào nhánh hiện tại; xung đột thì dừng, báo file. Không push.
- Worktree và nhánh của các bản thua: hỏi gộp trong cùng câu hỏi trên ("Merge B, xoá worktree A/C"); chỉ xoá khi được đồng ý.

## 5. Học

Nói một dòng kết quả. `CTT race-stats` cộng dồn tỉ lệ thắng và chi phí trung vị theo model·effort — gợi ý chỉnh `[roles]` / `[escalation]` của policy khi một model rẻ hơn thắng đều (≥ 5 lượt).
