# Reel reference

Các ảnh trong thư mục này chỉ dùng làm dữ liệu hồi quy cho detector, không dùng làm tọa độ cố định:

- `reel_action_rail_50x208.png`: vùng action rail thực tế do người dùng cung cấp trên LDPlayer 540x960, DPI 150.

Template gốc vẫn nằm trong `resources/templates/`:

- `nutlikereel.png.jpg`: matcher tự bỏ counter và crop sát icon ngón tay cái.
- `domcamxucreel.png`: màn Reel tham khảo có action rail bên phải.
- `camxucreel.jpg`: rail tham khảo cũ.

Reel locator nhận biết ảnh rail đã crop, không cắt tiếp 62% chiều rộng. Candidate chỉ được tap khi là icon đầu của chuỗi ít nhất ba icon action theo cột dọc; Share và Save không đủ cấu trúc phía dưới nên bị loại.
