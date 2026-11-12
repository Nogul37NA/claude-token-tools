---
description: Token và chi phí quy đổi theo ngày, project, phiên (đọc transcript local)
argument-hint: "[số ngày, mặc định 14]"
---

Cách chạy công cụ (gọi là `CTT` bên dưới): trên Windows `py -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`, nơi khác `python3 -I "${CLAUDE_PLUGIN_ROOT}/bin/ctt.py"`. Nếu không có Python ≥ 3.11 thì công cụ tự báo cách cài — chuyển nguyên thông báo đó cho người dùng và dừng. Nếu lệnh báo không tìm thấy file `bin/ctt.py`, hoặc đường dẫn còn chữ CLAUDE_PLUGIN_ROOT chưa được thay, báo người dùng là plugin cài chưa đúng và dừng. Đường dẫn là một thư mục bình thường thì cứ chạy.

Chạy `CTT report --days N`, với N là `$ARGUMENTS` nếu đó là một số, mặc định 14. Rồi chạy `CTT delegation --days N` và `CTT ship-report --days N` (cùng N).

Trình bày ngắn gọn bằng tiếng Việt: tổng token và chi phí quy đổi, 3 ngày tốn nhất, project tốn nhất, 5 phiên tốn nhất (tên phiên). Nói rõ chi phí là **giá API quy đổi**, không phải hoá đơn — trên gói thuê bao, ngân sách thật là % hạn mức (`/token-tools:token-limits`). Thêm mục **Giao việc cho agent** từ lệnh thứ hai: số agent, tổng tiết kiệm hay tốn thêm, agent lỗ nhiều nhất, và bảng hòa vốn (việc cần ≥ bao nhiêu lượt thì giao mới có lời, theo context phiên chính) — nói rõ đó chỉ là token và $, không tính việc làm lại, và mẫu còn nhỏ nếu dưới 30 agent. Thêm mục **Quy trình ship** từ lệnh thứ ba (bỏ qua nếu 0 lượt): số lượt, đạt/một phần/không đạt, tổng $, thực tế so với P50, gợi ý công cụ in ra, và đường dẫn trang HTML (gửi file ở chế độ hiển thị nếu có tool gửi file). Kết thúc bằng đường dẫn file CSV mà công cụ in ra. Không chạy thêm lệnh nào khác ngoài ba lệnh trên.
