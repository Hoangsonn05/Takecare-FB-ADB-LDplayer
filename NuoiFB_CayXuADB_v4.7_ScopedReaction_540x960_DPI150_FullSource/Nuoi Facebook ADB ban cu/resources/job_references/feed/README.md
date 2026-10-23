# Feed reference

Các ảnh trong thư mục này chỉ dùng làm dữ liệu hồi quy cho detector, không dùng làm tọa độ cố định:

- `feed_action_row_540x40.png`: vùng action thực tế do người dùng cung cấp trên LDPlayer 540x960, DPI 150.

Template gốc vẫn nằm trong `resources/templates/`:

- `like.png`: icon + nhãn Thích; matcher tự xóa đường kẻ ngang và crop sát foreground.
- `domnutlike.png`: hàng Like/Bình luận/Chia sẻ tham khảo.
- `camxuc.png`: bảng chọn cảm xúc Feed.

Feed locator chỉ tap candidate template khi đồng thời xác minh được ba cụm action cùng hàng và candidate thuộc cụm đầu bên trái.
