LDPlayer Facebook Automation Tool
    Tên file: ldplayer_fb_automation.py
    Mô tả: Công cụ Python đơn lẻ để khởi động LDPlayer, mở ứng dụng Facebook, lướt Newsfeed theo_logic chuẩn (1.5 vuốt xuống/bài, 10‑15 % vuốt lên‑xong‑xuống) và thả cảm xúc động qua OpenCV template‑matching (hoặc offset tĩnh).



    Mục lục

    1. Tổng quan
    2. Các tính năng chính
    3. Luồng hoạt động (logic)
    4. Cấu trúc thành phần bên trong
    5. Các hằng số và tham số có thể tùy chỉnh
    6. Cách cài đặt và chạy
    7. Mở rộng tool
    8. Ghi chú thường gặp & khắc phục
    9. Giấy phép



    <a name="tổng-quan"></a>
    1. Tổng quan

    - Mục đích: Tự động hoá việc “nuôi acc” Facebook trên LDPlayer bằng cách mô phỏng thao tác của người dùng thật (vuốt màn hình, thả cảm xúc) để tránh bị hệ thống phát hiện là bot.
    - Điểm mạnh:
      - Một file độc lập – không cần thư mục phụSkill hoặc nhiều script lẻ.
      - Chạy trên Windows – tất cả đường dẫn được viết theo kiểu Windows (có thể dùng WSL nếu muốn).
      - Dễ dàng tùy chỉnh – thay đổi tọa độ, thời gian, xác suất, danh sách cảm xúc, … chỉ qua đối số dòng lệnh hoặc sửa hằng số ở đầu file.
      - OpenCV template‑matching – tìm nút Like và các biểu tượng cảm xúc dựa trên hình ảnh mẫu, fallback về offset tĩnh nếu không tìm được.
      - Hàng đợi cảm xúc – khi cung cấp danh sách cảm xúc cụ thể, script sẽ xáo trộn danh sách và dùng từng loại một lần trước khi lặp lại, đảm bảo mỗi loại được dùng đều.

    <a name="các-tính-năng-chính"></a>
    2. Các tính năng chính

    | Nhóm | Tính năng | Mô tả |
    |------|-----------|-------|
    | Khởi động LDPlayer | ldconsole.exe launch --index=<ID> | Mở instance LDPlayer theo chỉ số truyền vào (--index). Đợi 8 s để LDPlayer khởi động hoàn toàn. |
    | Mở Facebook | ldconsole.exe runapp --index=<ID> --packagename=com.facebook.katana | Khởi chạy app Facebook và đợi 5 s để ứng dụng load xong. |
    | Kết nối ADB | Tự tính cổng localhost:5555 + 2*index và kiểm tra trạng thái | Đảm bảo ADB kết nối đúng instance trước khi thao tác touche/swipe. Nếu cần, script sẽ thực hiện adb connect tự động. |
    | Chụp màn hình & Template Matching | Dùng adb screencap → pull → OpenCV matchTemplate | - Tìm nút Like (template like.png).<br>- Khi cần thả cảm xúc khác, tìm template tương ứng (love, haha, wow, sad, angry, thuongthuong).<br>- Độ tin cậy mặc định 0.6 (60 %). Nếu không đạt, sẽ fallback về tọa độ tĩnh tính từ vị trí Like. |
    | Thao tác touche | ld.exe -s <index> input swipe … | Hỗ trợ: tap, long‑press (1 s) để mở panel cảm xúc, và swipe vuốt theo tọa độ/thời gian. |
    | Logic vuốt Newsfeed (chuẩn) | - Mỗi bài viết cần 1‑2 lần vuốt xuống (trung bình 1.5 lần/bài).<br>- 10‑15 % số bài có thêm một vuốt lên rồi vuốt xuống hoàn trả (rải rác, không tạo chu kỳ).<br>- Chỉ tính các vuốt xuống “tự nhiên” (không ngay sau một vuốt lên) để đếm bài viết. | Đây chính là thuật toán đã được mô tả trong skill nurture_scroll.py và được giữ nguyên trong file hợp nhất. |
    | Thả cảm xúc động | - Xác suất thả cảm xúc trên mỗi bài (--prob %, mặc định 10 %).<br>- Giới hạn số cảm xúc tối đa (--max-reactions).<br>- Loại cảm xúc có thể:<br>  • random – chọn ngẫu nhiên từ 7 loại.<br>  • Danh sách cụ thể (ví dụ like,love,haha) – script sẽ shuffle danh sách và dùng từng loại một lần trước khi lặp lại (hàng đợi cảm xúc).<br>- Thả cảm xúc luôn có độ trễ 1.5 s trước khi thực hiện, sau khi long‑press nút Like để mở panel. | Hỗ trợ đầy đủ 7 cảm xúc: like, love, thuongthuong, haha, wow, sad, angry. Mỗi cảm xúc có template ảnh riêng và offset tĩnh (60px/icon) dùng khi template matching thất bại. |
    | Cấu hình dễ thay đổi | Các thông số cơ bản (tọa độ vuốt, thời gian vuốt, độ trễ, offset cảm xúc) đều được đặt là hằng số ở đầu file – bạn có thể sửa ngay trong script mà không cần thay đổi logic. | Ngoài ra, bạn cũng có thể chỉ định folder chứa template qua --template-dir (mặc định là thư mục templates bên cạnh script). |
    | Logging & báo tiến độ | In ra terminal mỗi bước: vuốt, попытка thả cảm xúc, kết quả, số bài đã xử lý, tổng cảm xúc đã thả, thời gian đã chạy. | Khi kết thúc, script xuất báo cáo tổng kết: thời gian thực tế, số bài đã lướt, số cảm xúc đã gửi. |
    | Điểm mạnh khi di chuyển | - Một file độc lập – không cần thư mục phụSkills hoặc nhiều script lẻ.<br>- Không phụ thuộc vào môi trường WSL – tất cả các đường dẫn được chuyển sang Windows style (D:\…) và có thể chạy trực tiếp trên CMD/PowerShell.<br>- Dễ dàng tùy chỉnh – chỉ thay đổi đối số dòng lệnh hoặc một số hằng số ở đầu file. |  |

    <a name="luồng-hoạt-động-logic"></a>
    3. Luồng hoạt động (logic)

    1. Xử lý đối số dòng lệnh – lấy index, time, prob, max-reactions, reaction-type, ld-base, template-dir.
    2. Xác định đường dẫn tới ldconsole.exe, ld.exe, adb.exe và tới folder template.
    3. Tính ADB serial: localhost:<port> với port = 5555 + 2*index.
    4. Kiểm tra kết nối ADB – nếu trạng thái không phải device, thực hiện adb connect.
    5. Khởi động LDPlayer (nếu chưa chạy): ldconsole.exe launch --index=<index>.
    6. Mở Facebook: ldconsole.exe runapp --index=<index> --packagename=com.facebook.katana.
    7. Vòng lặp chính (chạy tới hết thời gian --time phút):
       - Quản lý bài viết – mỗi bài cần 1‑2 vuốt xuống (random).
       - Vuốt lên‑xuống ngẫu nhiên – với xác suất 10‑15 % mỗi batch bài, thực hiện một vuốt lên rồi ngay một vuốt xuống hoàn trả (không tính vào số bài).
       - Vuốt xuống – tạo tọa độ X (200‑340), Y1 (700‑800) → Y2 (220‑320), thời gian vuốt (350‑450 ms), độ trễ sau vuốt (2‑3 s).
       - Kiểm tra xác suất thả cảm xúc – chỉ trên vuốt xuống đầu tiên của mỗi bài. Nếu random ≤ --prob và chưa đạt --max-reactions:
         1. Chụp màn hình bằng adb screencap → pull.
         2. Tìm nút Like via template matching (like.png). Nếu không tìm được → bỏ qua.
         3. Long‑press nút Like (1 s) để mở panel cảm xúc, đợi 2.5 s.
         4. Chụp màn hình lại (panel hiện lên).
         5. Tìm template cảm xúc tương ứng (theo --reaction-type hoặc random). Nếu tìm được độ tin cậy ≥ 0.6 → tap vào vị trí đó; nếu không → dùng offset tĩnh từ vị trí Like (60px/icon).
         6. Tap vào cảm xúc, tăng bộ đếm reactions.
       - Log mỗi thao tác (vuốt, попытка thả cảm xúc, kết quả).
    8. Kết thúc vòng lặp – in báo cáo tổng kết: thời gian thực tế, số bài đã lướt, số cảm xúc đã gửi.
    9. Thoát – không còn tiến trình nền còn lại (tất cả các lệnh ld.exe và adb đều là foreground).

    <a name="cấu-trúc-thành-phần-bên-trong"></a>
    4. Cấu trúc thành phần bên trong

    text
    ldplayer_fb_automation.py
    │
    ├─ Hằng số cấu hình (đầu file)
    │   ├─ Đường dẫn LDPlayer (ld_base)
    │   ├─ Tọa độ vuốt (SWIPE_X_, SWIPE_Y_, SWIPE_DURATION_, SWIPE_DELAY_)
    │   ├─ Offset cảm xúc (REACTION_OFFSETS)
    │   ├─ Ngưỡng độ tin cậy (CONFIDENCE_LIKE, CONFIDENCE_REACTION)
    │   ├─ Tên file eseguibili (LD_EXE_NAME, LDCONSOLE_EXE_NAME, ADB_EXE_NAME)
    │
    ├─ Hàm tiện ích
    │   ├─ _resolve_path()               – ghép đường dẫn Windows an toàn
    │   ├─ ldconsole_cmd() / ld_cmd() / adb_cmd() – tạo chuỗi lệnh có dấu ngoặc kép
    │   ├─ launch_instance()             – gọi ldconsole launch
    │   ├─ open_facebook()               – gọi ldconsole runapp
    │   ├─ adb_serial()                  – tính cổng ADB theo index
    │   ├─ take_screenshot()             – screencap + pull về file tạm
    │   ├─ find_like_button()            – template match like.png
    │   ├─ find_reaction()               – template match reaction icon
    │   ├─ tap_ld() / long_press_ld() / swipe_ld() – wrapper quanh ld.exe input swipe
    │   ├─ random_swipe_down() / random_swipe_up() – sinh tham số vuốt ngẫu nhiên
    │   ├─ do_swipe()                    – thực hiện swipe + in log + sleep
    │   ├─ add_reaction()                – logic hoàn chỉnh để thả cảm xúc (template match → fallback offset)
    │   └─ scroll_newsfeed()             – vòng lặp chính (quản lý bài, vuốt, thả cảm xúc)
    │
    ├─ Hàm main()
    │   ├─ Phân tích đối số (argparse)
    │   ├─ Kiểm tra sự tồn tại của ld_base và template_dir
    │   ├─ Xây dựng dict template paths
    │   ├─ Kết nối ADB (kiểm tra + connect nếu cần)
    │   ├─ Gọi launch_instance() & open_facebook()
    │   └─ Gọi scroll_newsfeed() với các tham số đã được chuẩn hoá
    │
    └─ Khi chạy trực tiếp (if name == "main":) → main()


    Các file phụ (tùy chọn)
    - templates/ – thư mục chứa 8 file ảnh mẫu: like.png, love.jpg, thuongthuong.jpg, haha.jpg, wow.jpg, sad.jpg, angry.jpg.
      - Nếu không tìm thấy template cụ thể, script sẽ tự động chuyển sang sử dụng offset tĩnh (được định nghĩa trong REACTION_OFFSETS).

    <a name="các-hằng-số-và-tham-số-có-thể-tùy-chỉnh"></a>
    5. Các hằng số và tham số có thể tùy chỉnh

    | Nhóm | Tên (hằng số) | Mô tả | Phạm vi / giá trị đề xuất |
    |------|---------------|-------|---------------------------|
    | Đường dẫn | LD_BASE | Folder chứa ldconsole.exe, ld.exe, adb.exe. Mặc định: D:\MMO\QuanlyLDPlayer\LD9Mod-32bit\LD9Mod | Có thể thay đổi qua --ld-base. |
    | Vuốt | SWIPE_X_MIN, SWIPE_X_MAX | Tọa độ X bắt đầu/kết thúc vuốt (giữa màn hình). | 200‑340 (px) – giữ an toàn để tránh nhấn vào nút Like/Comment/Share ở lề. |
    | | SWIPE_Y_DOWN_1_MIN, SWIPE_Y_DOWN_1_MAX | Y đầu tiên của vuốt xuống (từ dưới lên). | 700‑800 |
    | | SWIPE_Y_DOWN_2_MIN, SWIPE_Y_DOWN_2_MAX | Y cuối cùng của vuốt xuống (lên trên). | 220‑320 |
    | | SWIPE_Y_UP_1_MIN, SWIPE_Y_UP_1_MAX | Y đầu vuốt lên (từ trên xuống). | 220‑320 |
    | | SWIPE_Y_UP_2_MIN, SWIPE_Y_UP_2_MAX | Y cuối vuốt lên (xuống dưới). | 700‑800 |
    | | SWIPE_DURATION_MIN, SWIPE_DURATION_MAX | Thời gian vuốt (ms). | 350‑450 (ms) – mô phỏng tốc độ người thật. |
    | | SWIPE_DELAY_MIN, SWIPE_DELAY_MAX | Đợi sau mỗi vuốt (s). | 2.0‑3.0 (s) – tạo cảm giác “đọc bài”. |
    | Cảm xúc | REACTION_OFFSETS | Dictionary offset (dx, dy) từ vị trí Like tới mỗi biểu tượng cảm xúc (dùng khi template match thất bại). | like:(0,0), love:(60,-70), thuongthuong:(90,-70), haha:(120,-70), wow:(180,-70), sad:(240,-70), angry:(300,-70). |
    | | CONFIDENCE_LIKE | Ngưỡng độ tin cậy để chấp nhận kết quả template match nút Like. | 0.6 (60 %). |
    | | CONFIDENCE_REACTION | Ngưỡng độ tin cậy cho template match các biểu tượng cảm xúc. | 0.6 (60 %). |
    | Thời gian chạy | DEFAULT_TIME (đối số --time) | Thời gian chạy tổng (phút). | Mặc định 30. |
    | Xác suất thả | DEFAULT_PROB (đối số --prob) | Xác suất (0‑100) thả cảm xúc trên mỗi bài. | Mặc định 10. |
    | Giới hạn réaction | DEFAULT_MAX_REACTIONS (đối số --max-reactions) | Số lượng cảm xúc tối đa được gửi. | Mặc định 20. |
    | Loại cảm xúc | DEFAULT_REACTION_TYPE (đối số --reaction-type) | Danh sách cảm xúc (cách nhau bằng dấu phẩy) hoặc từ khóa random. | Mặc định like,haha,love. |
    | Folder template | DEFAULT_TEMPLATE_DIR (đối số --template-dir) | Thư mục chứa ảnh template cảm xúc. | Mặc định là thư mục templates bên cạnh script. |

    > Lưu ý: Nếu thay đổi bất kỳ hằng số nào trong mã nguồn, hãy chạy lại script để đảm bảo giá trị mới có hiệu lực. Khi chỉ muốn thay đổi tạm thời, sử dụng các đối số dòng lệnh – chúng sẽ ghi đè lên các hằng số mặc định.

    <a name="cách-cài-đặt-và-chạy"></a>
    6. Cách cài đặt và chạy

    Yêu cầu hệ thống

    | Yêu cầu | Chi tiết |
    |---------|----------|
    | Hệ điều hành | Windows 10/11 (có thể chạy trong WSL nhưng không bắt buộc). |
    | Python | Phiên bản 3.8 – 3.12 (khuyên dùng 3.10+). |
    | Thư viện Python | opencv-python, numpy. |
    | LDPlayer | Phiên bản bất kỳ có ldconsole.exe, ld.exe, adb.exe. Đường dẫn mặc định là D:\MMO\QuanlyLDPlayer\LD9Mod-32bit\LD9Mod; nếu khác, chỉ định qua --ld-base. |
    | Ảnh template | 8 file ảnh (like.png, love.jpg, thuongthuong.jpg, haha.jpg, wow.jpg, sad.jpg, angry.jpg) đặt trong folder templates bên cạnh script hoặc chỉ định qua --template-dir. |

    Các bước cài đặt

    1. Clone hoặc sao chép file ldplayer_fb_automation.py vào một thư mục bất kỳ.
    2. (Tùy chọn) Tạo thư mục templates trong cùng thư mục và đặt 8 file ảnh mẫu vào đó.
    3. Mở Command Prompt hoặc PowerShell, chuyển đến thư mục chứa script.
    4. Cài đặt dependencies:

       bash
       pip install opencv-python numpy


    5. Chạy script với các đối số cần thiết. Ví dụ:

       bash
       python ldplayer_fb_automation.py ^
           --index 4 ^
           --time 30 ^
           --prob 10 ^
           --max-reactions 20 ^
           --reaction-type "like,love,haha,wow,sad,angry"


       - Dấu ^ là dòng tiếp trong CMD; trong PowerShell dùng   ` hoặc simplesmente viết tất cả trên một dòng.

    6. Script sẽ hiển thị log từng thao tác và, khi hết thời gian, in báo cáo tổng kết.

    Lưu ý về quyền

    - Đảm bảo tài khoản Windows có quyền thực thi ldconsole.exe, ld.exe, adb.exe.
    - Nếu sử dụng Antivirus/Firewall, có thể cần cho phép các thực thi này kết nối tới localhost:<port>.

    <a name="mở-rộng-tool"></a>
    7. Mở rộng tool

    Dưới đây là几点建议，帮助你在现有基础上添加新功能或修改现有行为。

    7.1 Thêm mới loại tương tác (ví dụ: commenting, sharing)

    1. Tạo hàm mới trong phần Hàm tiện ích, ví dụ:

       python
       def add_comment(base, index, text):
           # 1. Tìm ô comment (có thể dùng OCR hoặc template)
           # 2. Tap vào ô, gõ text qua adb shell input text
           # 3. Tap nút Send


    2. Điều kiện gọi – trong scroll_newsfeed(), sau khi xác định đã thả cảm xúc (hoặc luôn), thêm một nhánh kiểm tra xác suất comment (--comment-prob) và gọi hàm trên.

    3. Cập nhật CLI – thêm đối số mới (--comment-prob, --comment-text) và truyền vào hàm.

    7.2 Thay đổiLogic vuốt (ví dụ: dùng Bézier curve thay vì đường thẳng)

    - Điện Hàm swipe_ld() hiện tại sử dụng input swipe x1 y1 x2 y2 duration (đường thẳng).
    - Để sử dụng Bezier, bạn cần chia đường dài thành nhiều đoạn nhỏ và gọi input swipe nhiều lần (mỗi đoạn ~20‑30 ms).
    - Tạo hàm bezier_swipe(base, index, p0, p1, p2, p3, total_time) tính toán các điểm trung gian qua công thức Bézier cubic và lặp lại.
    - Thay do_swipe() gọi bezier_swipe thay vì swipe_ld.

    7.3 Hỗ trợ nhiều thiết bị song song

    - Đọc danh sách index từ file cấu hình (JSON/CSV) hoặc từ đối số --indices 0,2,4.
    - Vòng lặp chính sẽ spawn một subprocess (hoặc threading.Thread) cho mỗi index, mỗi luồng chạy một bản sao của scroll_newsfeed() với serial riêng.
    - Đảm bảo mỗi luồng dùng file screenshot tạm riêng (ví dụ: fb_screen_{index}_{ts}.png).

    7.4 Tích hợp logging vào file

    - Thêm import logging.
    - Cấu hình logger ở đầu main(): logging.basicConfig(filename='fb_automation.log', level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s').
    - Thay tất cả print() bằng logging.info() (giữ lại print cho các thông báo quan trọng nếu muốn hiển thị cả trên console).

    7.5 Cấu hình thông qua file yaml/json

    - Thay vì truyền nhiều đối số dòng lệnh, đọc file config (config.yaml) ở início main().
    - Định nghĩa schema chứa tất cả các phần trên (paths, swipe params, reaction configs).
    - Sử dụng thư viện PyYAML hoặc json để load.

    7.6 Unit test cho các hàm tiện ích

    - Sử dụng pytest.
    - Mock subprocess.run để kiểm tra các hàm như take_screenshot, find_like_button, add_reaction mà không cần thiết bị thực.
    - Đảm bảo độ bao phủ >= 80% cho logic core.

    <a name="ghi-chú-thường-gặp--khắc-phục"></a>
    8. Ghi chú thường gặp & khắc phục

    | Triệu chứng | Nguyên nhân khả | Cách khắc phục |
    |------------|----------------|----------------|
    | Lỗi “File not found: like.png” | Folder template không đúng hoặc file bị đổi tên. | Kiểm tra --template-dir hoặc đảm bảo thư mục templates nằm bên cạnh script và chứa file like.png. |
    | ADB offline / device not found | LDPlayer chưa khởi động hoặc port không đúng. | Đảm bảo đã gọi ldconsole launch --index=<index>; kiểm tra port bằng adb devices; nếu vẫn lỗi, chạy thủ công adb connect localhost:<port>. |
    | Không tìm thấy nút Like (confidence < 0.6) | Ánh sáng màn hình thay đổi, hoặc template không khớp với độ phân giải hiện tại. | - Thử chụp ảnh màn hình thực tế và so sánh với template.<br>- Tái chụp template với cùng độ phân giải (LDPlayer thường 720x1280 hoặc 1080x1920).<br>- Giảm CONFIDENCE_LIKE xuống 0.5 nếu muốn nhiều lần thử nhiều hơn (có thể tăng false positive). |
    | Thả cảm xúc sai vị trí (click vào Like thay vì biểu tượng khác) | Template reaction không được tìm thấy, script fallback về offset sai. | - Kiểm tra tên file và đường dẫn trong REACTION_TEMPLATES.<br>- Đảm bảo ảnh mẫu reaction có nền trong suốt hoặc rõ ràng; nếu sử dụng .jpg có thể mất thông tinAlpha.<br>- Thử tăng CONFIDENCE_REACTION hoặc cung cấp template tốt hơn. |
    | Script chạy quá chậm, treo ở một swipe | Lệnh ld.exe input swipe bị chặn vì LDPlayer đang trong trạng thái không phản hồi (ví dụ: đang hiển thị quảng cáo full‑screen). | Thêm timeout cho mỗi lệnh subprocess.run(..., timeout=10) và bắt subprocess.TimeoutExpired để retry hoặc bỏ qua bước đó. |
    | Nhiều lần thả cảm xúc liên tiếp mà không đợi 1.5 s | Lỗi logic trong add_reaction() – thời gian sleep được đặt sai vị trí. | Đảm bảo time.sleep(1.5) được gọi trước khi thực hiện tap hoặc long_press. Kiểm tra lại đoạn code trong add_reaction. |
    | Cảm giác “bot” vì vuốt quá đều | Không đủ ngẫu nhiên trong tọa độ hoặc thời gian. | Tăng phạm vi random (SWIPE_X_MIN/MAX, SWIPE_DELAY_MIN/MAX) hoặc thêm Gaussian noise vào thời gian vuốt. |

    <a name="giấy-phép"></a>
    9. Giấy phép

    - Đây là một mẫu tài nguyên miễn phí dành cho mục đích học tập và tự động hoá cá nhân.
    - Bạn được phép sao chép, sửa đổi và phân phối lại mã này dưới điều khoản MIT License (bạn có thể thêm bản dichiaration của mình nếu muốn).
    - Tác giả không chịu trách nhiệm cho bất kỳ hại hại nào phát sinh từ việc sử dụng công cụ này (vi phạm điều khoản dịch vụ của Facebook/LDPlayer, v.v.). Hãy sử dụng theo cách thuần túy và tôn trọng các quy định của nền tảng.



    Kết luận:
    File ldplayer_fb_automation.py cung cấp một nền tảng hoàn chỉnh để điều khiển LDPlayer qua ADB, thực hiện thao tác vuốt và thả cảm xúc trên Facebook. Với tài liệu này, bạn có thể hiểu rõ từng thành phần, thay đổi hoặc mở rộng tính năng dựa trên nhu cầu cá nhân của mình. Nếu gặp bất kỳ khó khăn nào khi triển khai hoặc muốn bổ sung tính năng mới, hãy tham khảo phần Mở rộng tool ở trên hoặc liên hệ để được hỗ trợ thêm. Chúc bạn thành công!