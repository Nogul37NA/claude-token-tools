# token-tools

Plugin `token-tools` (repo `claude-token-tools`; tên plugin không được bắt đầu bằng `claude-` — engine Claude Code 2.1.321 từ chối).

Quản lý token và chọn model cho Claude Code, dùng chung cho mọi project trên máy.
Python ≥ 3.11 thuần (không thư viện ngoài). Chỉ đọc dữ liệu local trong `~/.claude/projects`, không gửi đi đâu.

## Cài (plugin Claude Code)

```bash
claude plugin marketplace add https://github.com/Nogul37NA/claude-token-tools.git; claude plugin install token-tools@token-tools
```

Cập nhật: `claude plugin update token-tools@token-tools` (phải ghi đủ tên@marketplace). Plugin chỉ thêm ~130 token nạp sẵn mỗi phiên (`claude plugin details token-tools`).

Lệnh plugin luôn gọi kèm tên plugin (app không nhận tên rút gọn như `/token-estimate`).

| Lệnh trong phiên | Việc |
|---|---|
| `/token-tools:token-setup [thư mục…]` | Khai các thư mục chứa project của máy (lần đầu) |
| `/token-tools:token-report [ngày]` | Token và chi phí quy đổi theo ngày / project / phiên |
| `/token-tools:token-calibrate [ngày]` | P50/P90 theo loại task |
| `/token-tools:token-limits` | Ghi mốc % hạn mức gói ↔ $ quy đổi (app desktop tự đọc %) |
| `/token-tools:token-estimate <mô tả / Jira / file / link>` | Phiếu ước lượng trước khi làm: đọc lướt đầu vào (≤ 30k token) → hồ sơ task (bạn xác nhận) → P50/P90 token, $ quy đổi, % hạn mức tuần, model từng bước, rủi ro, ngân sách; duyệt thì hook báo ở 50/80/100% |
| `/token-tools:ship <yêu cầu / Jira / file / link>` | Quy trình chuẩn 1 yêu cầu → sản phẩm, 2 lần duyệt: 0 nhận yêu cầu (≤ `clarify_max` câu hỏi chặn việc, chốt tiêu chí xong) → 1 phiếu ước lượng → 2 bảng chia việc theo vai, mỗi bước `main` / `subagent` / `session` (phiên con chỉ cho bước lớn ≥ `session_min_tokens`, độc lập) — **duyệt lần 1** (hoặc tự duyệt khi phiếu ≤ `auto_approve_usd`) → 3 chạy song song → 4 kiểm chứng (tester + reviewer + đối chiếu tiêu chí, ≤ `fix_rounds` vòng sửa) → 5 giao, so chi phí thực với phiếu — **duyệt lần 2** (commit/merge) → 6 học (`ctt ship-finish`). Tiến độ hiện trên Phòng điều hành (`ctt ship-*` ghi `~/.claude/claude-token-tools/ship/`); `[workflow]` của `policy.toml` đặt các ngưỡng |
| `py -I bin/ctt.py ship-next <id>` · `ship-resume [id]` | (0.19.0) `ship-next`: bước nào chạy được ngay, đang chạy, còn chờ bước nào, lỗi. `ship-resume` không tham số liệt kê các lượt dở; có mã lượt thì gắn lượt vào phiên đang chạy lệnh và in từng bước đang ở đâu để làm tiếp (sau khi phiên cũ bị ngắt hoặc sau `/token-tools:handoff`) |
| `py -I bin/ctt.py ship-report [--days 14]` | Báo cáo các lượt `/token-tools:ship`: bảng ngắn + trang HTML `~/.claude/claude-token-tools/reports/ship-<ngày>.html` (thẻ số tổng, biểu đồ phiếu P50–P90 so với thực tế, cách chạy các bước, từng lượt mở ra xem kế hoạch và ghi chú, chỗ hay vướng: vai bị chặn, phiên con không mở được, bước lỗi; gợi ý chỉnh `[workflow]`). Chi phí gồm cả phiên con của lượt. Nút **Báo cáo ship** trên Phòng điều hành làm và mở trang; `/token-tools:token-report` có mục Quy trình ship |
| `/token-tools:handoff [chủ đề]` | Bàn giao sang phiên mới: soạn bản tóm tắt ≤ 2k token (trạng thái repo, quyết định đã chốt, việc tiếp, file liên quan), lưu `~/.claude/claude-token-tools/handoffs/<ngày>-<chủ-đề>.md`, hỏi rồi mới mở phiên mới kèm bản đó (ngoài app: in lệnh `claude` để tự chạy) |
| `/token-tools:sync-models` | Áp chính sách model cho agent + phiên chính — xem diff, đồng ý mới ghi |

**Hook (tự chạy, chỉ cảnh báo, không chặn):** context phiên vượt 200k token (nhắc lại mỗi +100k), phiên nghỉ quá 60 phút (lượt sau ghi lại toàn bộ cache — báo $ ước tính), một lần gọi tool trả > 8k token (nhắc cả Claude lọc output). Ngưỡng ở `[hooks]` trong policy; tắt nhanh: `CTT_HOOKS=off`. Đo trên transcript 58 MB: ~150 ms / ~110 ms mỗi lần.

**Phòng điều hành (`/control-room`, mod của engine Claude Code ≥ 2.1.3xx):** khung cố định cạnh khung chat, cập nhật 3 giây/lần — hạn mức 5 giờ / tuần, ngân sách phiếu (hoặc độ đầy context), chi phí phiên (số của ctt và số Claude Code tự báo, ghi rõ nguồn), mỗi agent một thẻ (trạng thái, model, số lượt, lỗi, token tách *xử lý* / *mới* (in + ghi cache + out) / % đọc lại cache, $, việc đang làm, 3 lần gọi gần nhất), nhật ký, nút "Xem log" mở log của agent ngay trên thẻ (việc giao, từng lần gọi tool, dòng đầu kết quả — lỗi tô đỏ, câu trả lời cuối; `bin/ctt.py agent-log` đọc phần cuối transcript riêng của agent) kèm nút "Mở transcript trong app" (chỉ chạy khi khung nằm cạnh khung chat). Log hiện 12 dòng, "Xem thêm" mở đủ 30. Nút "Xoá log" trên từng thẻ ẩn các dòng đã có của agent đó (dòng mới vẫn hiện, transcript không bị đụng, "Hiện lại" để xem lại); agent không còn trong danh sách của app và im 10 phút thì tính là xong. Khi context ≥ 60% có nút "Bàn giao sang phiên mới": điền sẵn `/token-tools:handoff` vào ô nhập (không tự gửi). Từ 0.9.0 vẽ bằng phần tử gốc của app (chữ, khung viền, nút, bảng Markdown) theo bề rộng khung: thẻ agent tự xếp nhiều cột khi khung rộng, lượt bị chặn / phải hỏi / lỗi trong 15 phút gần nhất nổi lên mục "Cần chú ý" ở đầu khung. Token từng agent do `bin/ctt.py agent-usage` đọc tăng dần, 15 giây/lần và chỉ khi khung đang mở; mod không đưa gì vào context của model.

**Quyền theo vai (M3, v0.8.0):** khung kiểm mỗi lần agent con gọi tool, *trước* khi tool chạy (`tool.check`), theo vai của agent (tên agent → vai qua `[agent_role]` của policy). Chỉ siết thêm: cái Claude Code đã chặn vẫn chặn; phiên chính và agent không có vai (general-purpose, Explore, agent của Workflow) không bị giới hạn. Mặc định: planner / research / reviewer chỉ đọc; developer ghi `**/src/main/**`, ghi `**/db/migration/**` phải hỏi; tester chỉ ghi `**/src/test/**`; docs ghi `docs/**`, `*.md`; `git push/commit/reset --hard`, `rm -r`, lệnh có `prod`… luôn hỏi. Lệnh Bash ghép (`&&`, `|`, `;`) xét từng đoạn; ghi ra file bằng `>` coi như ghi. Ngoài quyền thì *chặn* (model đọc lý do) hoặc *hỏi bạn* tuỳ vai; chỗ không ai trả lời được thì engine coi là chặn. Thẻ agent ghi số lần bị chặn / phải hỏi, nhật ký có dòng ⛔ / ❓, nút "Quyền hạn" hiện bảng quyền và lần chặn gần nhất. Sửa ngay trên khung (0.10.0): trong "Quyền hạn" mỗi vai có ô chọn ngoài quyền thì *chặn / hỏi bạn*, nút *Tạm tắt / Bật lại* (tắt vẫn hỏi lệnh nguy hiểm), *Về mặc định*, và thêm / xoá từng đường dẫn được ghi, phải hỏi, lệnh được chạy — áp dụng từ lượt gọi tool kế tiếp. Khung ghi vào `~/.claude/claude-token-tools/permissions.toml` (viết lại cả file, bản trước ở `permissions.toml.bak`) qua `py -I bin/ctt.py permissions-set`; xem bản gộp: `py -I bin/ctt.py permissions`. Tool MCP: xét từ 0.20.0 (xem dưới).

Đối chiếu $ (12/11/2026): cùng khoảng từ đầu phiên, Claude Code tự báo $31,59, ctt tính $54,27 theo bảng giá API (Opus 5.5 đọc cache $0,20). Giả thuyết gần nhất: Claude Code tính đọc cache $0,10 và ghi cache 1 giờ theo giá 5 phút. Với gói thuê bao, thước đo thật là % hạn mức.

## Dùng trực tiếp (không qua plugin)

```bash
py -I bin/ctt.py report --days 14        # token theo ngày / project / phiên
py -I bin/ctt.py calibrate --days 30     # P50/P90 token và chi phí theo loại task
py -I bin/ctt.py sync                    # xem diff policy model (dry run)
py -I bin/ctt.py sync --apply            # ghi
py -I bin/ctt.py limits --five-hour 22 --five-hour-reset 2026-11-11T11:20:00Z \
                        --week 65 --week-reset 2026-11-13T02:00:00Z   # ghi mốc % hạn mức gói ↔ $ quy đổi
py -I bin/ctt.py limits                  # xem trung vị $ / 1% hạn mức
py -I tests/run_tests.py                 # chạy test
```

## Cấu hình (2 lớp)

| Lớp | Vị trí | Ai sửa |
|---|---|---|
| Mặc định | `defaults/policy.toml`, `defaults/prices.toml`, `defaults/permissions.toml` (trong repo) | người phát hành tool |
| Riêng từng máy | `~/.claude/claude-token-tools/policy.toml`, `prices.toml`, `permissions.toml` — chỉ ghi phần cần đè (vd `[scan] roots`) | người dùng; cập nhật tool không ghi đè |

Sửa plugin xong muốn các phiên khác thấy: tăng `version` trong `.claude-plugin/plugin.json` → `claude plugin marketplace update token-tools` → `claude plugin update token-tools@token-tools` → khởi động lại app/phiên. Bản cài là bản sao theo version trong `~/.claude/plugins/cache/`, không đọc thẳng repo; `/reload-plugins` ở phiên đang chạy có thể vẫn giữ bản cũ.

Báo cáo ghi vào `~/.claude/claude-token-tools/reports/` (đổi bằng `--out`). Biến môi trường: `CTT_HOME`, `CTT_CLAUDE_PROJECTS`.

## Cấu trúc

| Module | Việc |
|---|---|
| `ctt/transcripts.py` | Đọc transcript: đếm mỗi request 1 lần dù bị tách nhiều dòng, tách cache write 5m/1h, dòng hỏng chỉ đếm không làm dừng |
| `ctt/pricing.py` | Bảng giá theo tiền tố model id dài nhất; model lạ tính theo fallback và bị báo |
| `ctt/sessions.py` | Gộp theo phiên, đếm file nguồn đã sửa, migration, build, commit; gán loại task |
| `ctt/policy.py` | Ghi `model:`/`effort:` vào frontmatter agent (không động prompt) và `"model"` vào settings.json |
| `ctt/reports.py` | Báo cáo text + CSV |
| `ctt/permissions.py` | Gộp + kiểm `permissions.toml`, kèm `[agent_role]` của policy → JSON cho khung |
| `hooks/permissions.ts` | Xét quyền theo vai ở `tool.check` (glob đường dẫn, tách lệnh ghép, `always_ask`) |

## Lưu ý

- Agent cùng tên ở project **thay thế toàn bộ** agent ở `~/.claude/agents` (không gộp) → model phải ghi vào file của project; đó là lý do có `sync`.
- Chi phí là **giá API quy đổi**. Phiên chính ghi cache TTL 1h (2× input), subagent TTL 5 phút (1,25×). Giá đọc cache có `assumed = true` trong `prices.toml` là giả định — đối chiếu ở GĐ2.
- Loại task gán **sau khi làm** (số file đã sửa, số lượt). Ước lượng trước khi làm: GĐ5.
- Claude Code tự xoá transcript cũ theo `cleanupPeriodDays` — mốc ban đầu phải lưu thành CSV, không tính lại được về sau.

## Kết quả đối chiếu (GĐ2, 11/11/2026)

- **Token: lệch 0%.** App desktop báo context 380.862 token đúng bằng `input + cache_read + cache_write` của request tương ứng trong transcript.
- **$ do Claude Code tự báo: chưa lấy được trên máy dev** — app desktop không chạy status line; CLI `claude -p` chưa đăng nhập. Status line (`~/.claude/statusline.ps1`) đã ghi log `cost_log.jsonl` cho phiên chạy trong terminal, sẽ có số khi dùng CLI.
- **Gói thuê bao (Team): ngân sách thật là % hạn mức**, không phải $. `ctt limits` ghi cặp (% app hiển thị, $ quy đổi cùng cửa sổ). Mốc đầu: 5 giờ ≈ $0,66 / 1%, tuần ≈ $2,29 / 1% (cận dưới — dùng claude.ai hay máy khác cũng tính vào hạn mức). Cần ≥ 5 mốc ở các thời điểm khác nhau mới dùng để ước lượng.

## Giao việc cho agent có lời không (0.11.0)

`py -I bin/ctt.py delegation --days 30 [--main-model claude-fable-5-2]` (cũng nằm trong `/token-tools:token-report`): với mỗi agent con, so *đã giao* ($ của agent + phiên chính ghi và đọc lại bản báo cáo) với *tự làm* (cùng số lượt, mỗi lượt đọc lại context phiên chính lúc giao, cộng phần output tool lẽ ra nằm lại trong context phiên chính và bị đọc lại ở mọi lượt sau). Cuối báo cáo là bảng hòa vốn: việc cần ≥ bao nhiêu lượt thì giao mới có lời, theo context phiên chính (100k–600k), cho agent chạy cùng model phiên chính và chạy Sonnet. Chỉ so token và $, không thấy việc phải làm lại.

Đo 12/11/2026 (15 agent, 30 ngày): giao việc tiết kiệm ~$96, 12/15 agent có lời; 3 agent lỗ đều là việc 1–2 lượt khi phiên chính ~100k. Phiên chính Fable 5.2: giao cho Sonnet có lời từ ≥ 3 lượt (100k) / ≥ 2 lượt (≥ 200k); giao cho Fable từ ≥ 7 lượt (100k) xuống ≥ 3 lượt (600k). Agent khởi động trung vị ~40k token context (78k ở các phiên của một repo lớn gần đây).

**Gợi ý giao việc trên khung (0.12.0):** ngưỡng lấy từ `ctt delegation --json` (đo ≥ 5 agent, không đủ thì dùng `[delegation]` của policy). Khi phiên chính tự làm đã đủ số lượt ở context hiện tại, mục "Cần chú ý" hiện 💡 *giao phần còn lại cho agent Sonnet sẽ rẻ hơn* (một lần mỗi lượt, tự ẩn khi lượt xong). Agent trả lời xong với ít lượt hơn ngưỡng thì nhật ký ghi ↯ và thẻ có nhãn *tự làm rẻ hơn*. Chỉ hiện trên khung; `[delegation] hint_model = true` thì thêm một đoạn quy tắc giao việc (cố định cả phiên, không phá cache) vào system prompt phiên chính. Tắt nhắc: `nudge = false`.

**Ngân sách từng agent (M4, 0.13.0):** có phiếu ước lượng đã duyệt thì ngân sách agent = ngân sách phiếu × tỉ lệ bước của vai, chia đều cho các agent cùng vai; không có phiếu thì dùng trần `[budget.roles]` trong policy (mặc định trống = không giới hạn). Thẻ agent có thanh ngân sách; qua 50% ghi ◔, 80% ⚠ (lên "Cần chú ý"), 100% ‼ và từ đó mỗi lượt gọi tool của agent phải hỏi bạn. Nút *Tăng ngân sách* (+`raise_share` × ngân sách đầu, mặc định 50%), *Tạm dừng / Tiếp tục* (chặn mềm các lượt tiếp theo — app không có lệnh dừng agent; lệnh nộp báo cáo SubagentHandback luôn được chạy). Agent chạy model khác `[roles]` thì nhật ký ghi ↪; `[budget] enforce_model = true` thì đổi về model của vai lúc tạo agent.

**Nhiều phiên (M5, 0.14.0):** mỗi phiên ghi bản tóm tắt (project, đang làm/rảnh, agent đang chạy, context, $, lỗi/bị chặn gần nhất, câu hỏi gần nhất) vào kho chung `$.store` của plugin (một file JSON trên máy, `~/.claude/plugins/store/`), tối đa 10 giây/lần; bản cũ hơn 24 giờ tự xoá. Nút *Tất cả phiên (N)* trên khung liệt kê các phiên có hoạt động trong 10 phút và tổng $. Nút *Hỏi phiên khác* trên thẻ agent soạn sẵn câu hỏi (agent, việc, vấn đề, 5 bước gần nhất) vào ô nhập để sửa, chọn phiên nhận, bấm *Gửi* mới gửi (`$.session.send({ sessionId })`; phiên không chạy thì báo lý do). Tin phiên khác gửi tới hiện ở *Hộp thư* và vẫn vào phiên như thường. Mở phiên khác vẫn bằng thanh bên của app.

**Cây phiên và hỏi đáp có mã (0.16.0):** khi Claude mở phiên con (`start_session`), plugin ghi một mục chờ (`pending-child:` trong kho chung: phiên cha, tầng, 160 ký tự đầu lời giao việc); phiên con nhận ra lời giao việc của mình ở lượt đầu, lấy mục đó và ghi phiên cha, tầng, chế độ quyền vào bản tóm tắt. Lời giao việc không bị sửa (0.17.6 — sửa đầu vào sau khi chế độ Auto đã duyệt làm lần mở đầu luôn bị từ chối); dấu `[tt-parent:<id> tầng:<n>]` viết tay trong lời giao việc vẫn được hiểu. *Tất cả phiên* vẽ thành cây (phiên con thụt vào dưới phiên cha, $ cả nhánh, ⚠ khi phiên con khác chế độ quyền với phiên cha — Claude Code giữ lại tin giữa hai phiên như vậy). Phiên cha không đọc transcript phiên con, chỉ nhận báo cáo ngắn khi phiên con xong lượt. `[sessions]` trong `policy.toml` đặt trần `max_depth` (mặc định 2 tầng) và `max_children` (4 phiên con đang mở): vượt trần thì lệnh mở phiên bị từ chối kèm lý do, nhật ký ghi ⛔. Bàn giao (`hand_off_to_session`) giữ nguyên phiên cha và tầng. Mọi tin rời phiên (SendMessage của phiên chính hoặc agent, nút trên khung) được gắn `[q:<mã> từ:<địa chỉ>]` và lời nhắc trả lời bằng `[re:<mã>]`; khung có khối *Câu hỏi đã gửi* (⏳ chờ / ✓ đã trả lời kèm câu trả lời), *Hộp thư* có nút *Trả lời* gửi về đúng phiên hoặc agent đã hỏi. Nút *Hỏi phiên / agent* trên thẻ gửi được tới phiên cha, phiên con, phiên khác, hoặc agent khác ngay trong phiên này (`$.session.send({ agentId })` — rẻ nhất, không bị giữ tin vì lệch chế độ quyền). Quy tắc giao việc: việc ngắn → subagent; việc lớn, độc lập, sống lâu → phiên con cùng chế độ quyền.

**Ship chạy chắc hơn (0.19.0):** bảng chia việc có cột *Chờ bước* (`needs`) — `ship-stage --plan` từ chối tên bước trùng, tên không có và các bước chờ nhau thành vòng; `ship-step --status running` từ chối bước còn đang chờ. Mỗi lần bắt đầu một bước là một **lần thử có mã** (`a1-3f9c`): báo `done` / `failed` phải kèm `--attempt <mã>`, mã của lần thử cũ bị từ chối nên báo cáo đến muộn của agent đã bị thay không đóng nhầm bước; số lần thử được đếm và hiện trong báo cáo ship. Báo kết thúc nhận thêm `--file` (lặp được) và `--tests` — bước kiểm chứng đọc lại thay vì dò diff. Lượt cũ (trước 0.19.0) và bước chưa từng `running` vẫn báo `done` không cần mã.

**Tự xử lý khi bước hỏng (0.20.0):**
- *Nâng model*: `[escalation]` của policy (trước chỉ khai, chưa dùng) nay được `/token-tools:ship` dùng. Một bước lỗi đủ `fail_threshold` lần thì `ship-step --status failed` in model và effort của nấc kế trong `ladder`, tính từ nấc của vai trong `[roles]` (vai không có trên thang thì từ nấc đầu của model đó, không có model đó thì từ đầu thang); hết thang thì bước dừng, ghi thành việc mở. Lần thử chạy ở nấc nào được ghi lại trên lượt.
- *Bước treo*: bước đang ghi là chạy mà transcript của phiên, các agent và các phiên con đều không được ghi thêm trong `[workflow] stall_minutes` (mặc định 10, `0` = tắt) thì mục "Cần chú ý" của Phòng điều hành báo ⚠ kèm số phút. Chỉ là cảnh báo: một lệnh build chạy lâu hơn ngưỡng cũng bị báo.
- *Quyền tool MCP theo vai*: tool `mcp__…` mà engine không đánh dấu chỉ đọc nay được xét. `[always_ask] mcp` là các biểu thức trên tên tool (mặc định: delete / remove / trash / destroy / drop / purge, send / publish / merge / deploy) — khớp là hỏi bạn, với mọi agent có vai. Mỗi vai có thêm `mcp = "allow" | "ask" | "deny"` (mặc định `allow`, như trước) và `mcp_allow` (glob tên tool luôn cho chạy); sửa bằng `py -I bin/ctt.py permissions-set --role <vai> --mcp ask --add mcp_allow "mcp__jira__get_*"` hoặc trong `permissions.toml`. Chưa có ô chọn trên khung. Tool nội bộ của app (`mcp__ccd_*`) không bị xét.
- *`prompt.compose`*: mục quy tắc giao việc đổi id thành `token-tools:delegation` (id không có tiền tố plugin là của engine). Trên máy có plugin bảo mật do tổ chức cài (`cc-plugin-sec-default`), engine báo hook này bị bỏ qua vì plugin đó chuyển sự kiện đi vòng qua các plugin khác — `[delegation] hint_model = true` không có tác dụng ở đó; plugin không tìm cách vượt qua.

**Cổng quyết định (0.21.0):** một câu hỏi chỉ bạn trả lời được nay vướng đúng một bước thay vì dừng cả lượt. `py -I bin/ctt.py ship-gate <id> --step <bước> --question "…" --option A --option B` mở cổng (bỏ `--option` = trả lời tự do); bước đó và các bước chờ nó dừng, các bước khác chạy tiếp. Phòng điều hành hiện câu hỏi ngay dưới dòng quy trình kèm một nút cho mỗi lựa chọn: bấm là khung ghi quyết định (`ship-gate <id> --resolve <mã> --answer …`) và điền sẵn một dòng vào ô nhập để bạn gửi cho Claude (không tự gửi). Trả lời trong chat cũng được — Claude ghi quyết định. `ship-next` và `ship-resume` in cổng đang chờ và quyết định đã có; trang báo cáo ship liệt kê các cổng của từng lượt. `ship-gate <id>` không tham số liệt kê cổng.

**Review ngoài và ghi chú vai (0.22.0):**
- *Review bằng nhà cung cấp khác*: `py -I bin/ctt.py ship-review <id> [--base HEAD]` đưa diff của lượt (thay đổi so với `--base`, kèm file mới chưa track) và tiêu chí xong của từng bước cho một agent CLI khác qua stdin, giữ câu trả lời trên lượt (`external_review`) và in ra cho bước kiểm chứng. **Mặc định tắt**: bật bằng `[review] external = true` và `command = [...]` trong policy riêng của máy — bật là gửi diff sang dịch vụ đó. Bạn tự chọn chương trình và tham số chỉ đọc của nó; token-tools không kiểm được chương trình đó làm gì. Không tìm thấy chương trình, quá `timeout_seconds` hay mã thoát khác 0 thì bước này bị bỏ qua, lượt không dừng. Câu trả lời được đưa cho Claude như dữ liệu cần kiểm lại, không phải chỉ dẫn. Chưa chạy với CLI thật nào (máy phát triển không cài CLI agent khác) — mới test bằng chương trình giả.
- *Khi nào dùng vai nào*: `[role_notes]` trong policy là một dòng cho mỗi vai; `ship-start` in mục `roles` (model, effort, ghi chú) và `/token-tools:ship` chọn vai cho từng bước theo đó.

**Đường dẫn dài và chế độ Auto (0.22.2, 0.23.0):**
- *Đường dẫn dài trên Windows (0.22.2)*: phiên mở không chọn thư mục có tên thư mục project ~190 ký tự, nên transcript của agent con nằm quá 260 ký tự và `ctt` không đọc được — token từng agent và ngân sách agent luôn bằng 0, báo cáo đếm thiếu. Nay mọi chỗ đọc transcript dùng dạng đường dẫn dài của Windows.
- *Chế độ Auto (0.23.0)*: đo 18/11/2026 trong một phiên ở chế độ quyền Auto — quy tắc "chặn" của vai vẫn chặn, nhưng mọi chỗ trả lời "hỏi bạn" (lệnh luôn hỏi như `git commit` / `git push` / lệnh có `prod`, `ask_write`, `outside = "ask"`, agent hết ngân sách) **không hiện hộp hỏi nào và lệnh vẫn chạy**. Nguyên nhân chưa được xác nhận; nhiều khả năng câu hỏi của hook được giao cho bộ phân loại của chế độ Auto. Vì vậy có `[auto_mode] ask` trong `permissions.toml`: `"ask"` (mặc định, như cũ) hoặc `"deny"` — trong phiên Auto mọi chỗ lẽ ra hỏi thành chặn, agent đọc lý do và ghi việc đó vào báo cáo để bạn tự làm. Đổi lại agent có vai không commit, push hay ghi migration được trong phiên Auto. Chế độ lấy từ lúc agent được tạo (chế độ của phiên chính lúc đó). Bật: thêm vào `~/.claude/claude-token-tools/permissions.toml`:
  ```toml
  [auto_mode]
  ask = "deny"
  ```
  Phiên chính không có vai nên không bị quy tắc nào ở đây chạm tới, ở chế độ nào cũng vậy.

**Bộ agent theo vai trong plugin (0.15.0):** `agents/` có 8 agent gọi được ở mọi project: `token-tools:planner`, `architect`, `research`, `developer`, `debugger`, `tester`, `reviewer`, `docs` — model/effort theo `[roles]` của policy (test giữ hai bên khớp), khởi động bằng quy ước của project đang mở (`CLAUDE.md`, `AGENTS.md`, `ai-rules/AGENT.md`), làm đúng phạm vi vai, báo cáo ngắn. Agent riêng của project (`.claude/agents`) vẫn dùng như cũ. `py -I bin/ctt.py agents --cwd <repo>` liệt kê agent gọi được theo mức project / người dùng / plugin; khung ghi mức cạnh model trên thẻ và liệt kê agent của từng vai trong "Quyền hạn".

## Phiếu ước lượng (GĐ5)

```bash
py -I bin/ctt.py estimate --profile hoso.json [--cwd REPO] [--week-used 65]   # ra phiếu + trang HTML local
py -I bin/ctt.py estimate-approve <id>      # duyệt ngân sách → hook theo dõi phiên
py -I bin/ctt.py estimate-check             # so phiếu với thực tế
py -I bin/ctt.py estimate-backtest --days 60   # chạy ngược trên phiên cũ (bỏ chính phiên đó ra)
```

Tham số: `defaults/estimate.toml` (nhóm, hệ số ước số file, chia bước, ngân sách), `defaults/risks.toml` (10 rủi ro). Chạy ngược 11/11/2026 trên 85 phiên: 88% ≤ P90 (phiên 1 ngày 100%, nhiều ngày 74%), trung vị thực tế/P50 = 1,02 — dùng số file thật nên là cận trên độ chính xác.

## Lộ trình

GĐ0 mốc ban đầu ✅ · GĐ1 lõi + test ✅ · GĐ2 đối chiếu số liệu (token ✅, $ chờ CLI) · GĐ3 plugin ✅ · GĐ4 hook cảnh báo ✅ · GĐ5 phiếu ước lượng ✅ · GĐ5 phiếu ước lượng · GĐ6 đo hiệu quả 2 tuần · GĐ7 thử nghiệm 2–3 người.
