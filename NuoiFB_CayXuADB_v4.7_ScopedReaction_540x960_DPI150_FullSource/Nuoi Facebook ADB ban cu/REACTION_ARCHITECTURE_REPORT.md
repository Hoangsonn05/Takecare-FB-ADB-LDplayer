# Báo cáo kiến trúc reaction v4.7

## Mã nguồn và entry point

- Mã tham khảo, chỉ đọc: `TTC-Mulitap`.
- Mã ADB được sửa: thư mục ứng dụng `Nuoi Facebook ADB ban cu` trong gói `NuoiFB_CayXuADB_v4.6_FixLike_540x960_DPI150_FullSource`.
- Entry point GUI: `app.py` -> `gui.main_window.MainWindow`.
- Entry point CLI: `cli.py` -> `automation.scheduler.AutomationScheduler`.
- Auto Job thực tế: `JobRunner` -> `ReactionExecutor` -> detector/ADB -> `ActionVerifier`.

## Luồng trước khi sửa

```text
locate Like -> kiểm tra lại khoảng cách tọa độ
-> tap hoặc long press
-> xác định panel bằng XML hoặc bảy vòng tròn
-> chọn option theo _FEED_ORDER/_REEL_ORDER hoặc một template
-> xác nhận selected bất kỳ trên toàn XML hoặc thay đổi pixel cục bộ
```

Các điểm rủi ro chính: anchor không có danh tính scope; selected không giới hạn trong bài/Reel; panel không dùng delta trước/sau; thứ tự Reel bị đảo cố định; pixel cục bộ có thể xác nhận sai loại reaction; không có pre-check chắc chắn để tránh Like -> Unlike.

## Luồng sau khi sửa

```text
Feed/Reel strategy resolve anchor + action cluster + scope signature
-> scoped pre-check
-> re-resolve content kind + cluster + signature + movement
-> tap Like khi thật sự NONE, hoặc long press để chọn/đổi reaction
-> resolve panel mới xuất hiện và gần đúng anchor
-> exact XML option, hoặc reaction template trong panel đã xác nhận
-> scoped verifier
-> detected_active_reaction == requested_reaction
-> CONFIRMED / REACTION_TYPE_MISMATCH / UNKNOWN
```

## Trách nhiệm module

- `detectors/ui_tree.py`: parse XML, cha/con, clickable ancestor, bounds, truy vấn node/scope và action layout.
- `detectors/feed_like.py`: candidate và validation hàng ngang Post.
- `detectors/reel_like.py`: candidate và validation rail dọc Reel.
- `detectors/reaction_resolver.py`: chuẩn hóa nhãn Anh/Việt và đọc trạng thái trong đúng anchor/scope.
- `detectors/reaction_panel.py`: gắn panel với anchor theo before/after và resolve option trong panel.
- `executors/reaction.py`: điều phối resolve -> pre-check -> re-resolve -> ADB -> panel -> verifier.
- `action_verifier.py`: so reaction thực tế với reaction yêu cầu; không thực hiện ADB.
- `evidence.py`: ghi bundle chẩn đoán khi lỗi, đồng thời lọc text XML không liên quan.

## Interface thay đổi

- `LocateResult` được mở rộng tương thích ngược với `content_kind`, bounds Reaction/Comment/Share, action cluster, scope, frame ID, scope signature và candidate evidence.
- `ReactionPanelResolution` chứa panel bounds, anchor bounds, options, source, confidence và frame ID.
- `ReactionState` phân biệt `NONE`, `ACTIVE`, `UNKNOWN`.
- `ActionVerification` có `requested_reaction` và `detected_active_reaction`.
- Thêm failure code `REACTION_TYPE_MISMATCH`.

## Nguyên lý học từ TTC

### Đã tích hợp

- Tách Post và Reel thành chiến lược hình học riêng.
- Clickable ancestor có giới hạn, action cluster và scope.
- Kiểm tra lại element/scope trước thao tác.
- Panel phải có quan hệ không gian và thời gian với anchor.
- Ưu tiên nhãn chính xác, sau đó mới dùng visual candidate trong panel.
- Pre-check và exact post-check theo đúng loại reaction.
- Retry có giới hạn và resolve lại từ đầu.
- Pixel change chỉ là chẩn đoán.

### Không thể tích hợp trực tiếp

- DOM selector, Playwright locator/handle, JavaScript `elementFromPoint`, hover và browser URL scope.
- Các phần này được thay bằng UIAutomator XML, screenshot/OpenCV và ADB tap/long press.

### Không cần tích hợp

- TTC API/browser session, profile browser, dispatcher và logic claim TTC.
- ADB giữ nguyên GoLike API, queue, account binding, navigation và job lifecycle hiện tại.

## Phần cũ đã loại bỏ

- Constant `_FEED_ORDER` và `_REEL_ORDER`.
- Nhánh `panel_geometry` chọn reaction chỉ bằng index.
- Xác nhận `two_stable_local_observations` chỉ dựa layout + pixel.
- Tìm selected reaction trên toàn XML không giới hạn anchor.
- Kiểm tra stale chỉ bằng khoảng cách tọa độ.
- Không xóa file/class công khai nào; không thêm dependency.

## Chẩn đoán

Khi bật lưu evidence và reaction thất bại, bundle có thể gồm `before.png/xml`, `after_long_press.png/xml`, `panel.png/xml`, `after_action.png/xml`, `resolution.json`, `verification.json`, `candidate_rejections.json`. XML được lọc để bỏ text không liên quan đến action/reaction.

## Giới hạn runtime

Test tự động không thay thế được kiểm chứng trên Facebook/LDPlayer thật. Thay đổi accessibility theo phiên bản Facebook có thể khiến trạng thái trở thành `UNKNOWN`; đây là hành vi fail-safe và không tap.
