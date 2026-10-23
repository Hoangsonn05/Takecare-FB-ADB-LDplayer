# Bản vá xác định nút cảm xúc — 540 × 960, DPI 150

## Mục tiêu

Bản vá sửa lỗi OpenCV xác định nhầm hoặc không tìm thấy nút cảm xúc trong hai giao diện Facebook:

- **Post/Feed:** hàng ngang `Thích – Bình luận – Chia sẻ`.
- **Reel:** cột dọc gồm nút cảm xúc, chia sẻ và lưu.

Cấu hình thiết bị mặc định dùng cho việc quy đổi kích thước giao diện:

- Chiều ngang: **540 px**
- Chiều dọc: **960 px**
- DPI: **150**

Các giá trị trên là cấu hình chuẩn hóa, không phải tọa độ nhấn cố định. Bộ nhận dạng vẫn dựa trên icon, cấu trúc tương đối và vùng thao tác thực tế của từng ảnh chụp màn hình.

## Các thay đổi chính

### 1. `core/template_matcher.py`

- Làm sạch template Feed bằng Otsu threshold.
- Loại bỏ đường phân cách ngang dài khỏi template và ảnh tìm kiếm.
- Cắt sát phần foreground của icon/chữ thay vì đối chiếu cả nền trắng.
- Làm sạch template Reel, loại phần số đếm và vùng nền thừa.
- Tìm nhiều candidate theo nhiều tỉ lệ rồi thực hiện non-maximum suppression.
- Hỗ trợ ảnh toàn màn hình và ảnh rail Reel đã crop.
- Kết quả match có thêm kích thước, tỉ lệ và bounds để detector kiểm tra cấu trúc.

### 2. `automation/jobs/detectors/feed_like.py`

- Ưu tiên UI hierarchy khi có dữ liệu đáng tin cậy.
- OpenCV chỉ tạo candidate; detector Feed mới quyết định candidate hợp lệ.
- Candidate phải là cụm đầu tiên trong hàng ba vùng thao tác cùng trục Y.
- Không dùng contour để tự sinh tọa độ nhấn.

### 3. `automation/jobs/detectors/reel_like.py`

- Nhận biết rail dọc đã crop để không cắt ROI lần hai.
- Candidate phải là icon đầu tiên trong chuỗi hành động dọc.
- Bắt buộc có các icon thao tác bên dưới với khoảng cách hợp lý.
- Loại trường hợp matcher rơi xuống nút Chia sẻ hoặc Lưu.

### 4. `automation/jobs/detectors/ui_tree.py`

- Parse cây XML theo quan hệ cha/con.
- Node chữ hoặc số đếm được ánh xạ lên clickable ancestor gần nhất.
- Bắt buộc Reaction, Comment và Share có vùng clickable riêng biệt.
- Dùng đối chiếu cụm từ có ranh giới thay cho substring tự do.

### 5. `automation/jobs/action_verifier.py`

- Không còn coi thay đổi pixel chung là bằng chứng thành công.
- Xác nhận khi UI node chuyển `selected/checked`, hoặc khi hai frame liên tiếp cho thấy bố cục ổn định và thay đổi tập trung tại nút cảm xúc.
- Panel bình luận/chia sẻ hoặc thay đổi diện rộng sẽ không được xác nhận nhầm là đã thả cảm xúc.

### 6. Cấu hình thiết bị

Ba trường sau đã được thêm đồng bộ vào cấu hình runtime, cấu hình Auto Job, file JSON mặc định và giao diện:

```json
{
  "screen_width_px": 540,
  "screen_height_px": 960,
  "screen_dpi": 150
}
```

## Kiến trúc được giữ nguyên

Luồng vẫn là:

```text
Runner → Executor → Locator/Detector → ADB tap → ActionVerifier
```

Không đưa logic nhận dạng vào Runner, không tạo tọa độ hardcode và không thêm controller/service trùng trách nhiệm.

## Kiểm thử

Đã chạy:

```text
python -m compileall -q .
python -m unittest tests.test_detectors tests.test_models_and_security tests.test_navigation tests.test_runner_integration
```

Kết quả: **54 test vượt qua**.

Các regression test mới bao gồm:

- Ảnh Feed 540 px chọn đúng cụm Thích bên trái.
- Ảnh Reel crop chọn icon trên cùng.
- Khi xóa icon Like khỏi ảnh Reel, detector không rơi xuống nút Lưu.
- UI text/counter được ánh xạ lên clickable ancestor.
- Thay đổi pixel đơn thuần không được xác nhận là thành công.

Test giao diện PySide6 không chạy trong môi trường đóng gói hiện tại vì container không cài PySide6. Dependency `PySide6>=6.6` vẫn có trong `requirements.txt`; toàn bộ source đã qua kiểm tra compile.
