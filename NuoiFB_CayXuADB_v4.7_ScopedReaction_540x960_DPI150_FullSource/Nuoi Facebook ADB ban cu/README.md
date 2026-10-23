# Nuôi Facebook ADB + Auto làm job

Ứng dụng PySide6 điều khiển nhiều LDPlayer qua ADB với hai luồng độc lập:

- `Nuôi tài khoản`: giữ nguyên workflow Newfeed, Reel và Notifications hiện có.
- `Auto làm job`: xác thực GoLike, bind rõ LDPlayer với tài khoản Facebook và thực hiện reaction job theo action API.

Auto Job không gọi các hàm reaction ngẫu nhiên của `FeedRunner`. Mỗi worker dùng API session riêng, không lưu AuthToken/signing key và không Submit khi thao tác chưa được xác minh.

## Cài dependency

```powershell
cd "Nuôi Facebook ADB bản cũ"
python -m pip install -r requirements.txt
```

Dependency chính:

- PySide6
- opencv-python
- numpy
- requests
- cryptography

## Chạy ứng dụng

```powershell
python app.py
```

Thư mục LDPlayer phải chứa:

```text
ldconsole.exe
ld.exe
adb.exe
```

Package Facebook được cố định là `com.facebook.katana`.

## Luồng Nuôi tài khoản

1. Chọn hoặc nhập thư mục LDPlayer.
2. Quét LD hoặc nhập index thủ công.
3. Chọn Newfeed/Reel/Notifications và cấu hình thời gian.
4. Chọn LD trong bảng rồi bấm `Start Selected`.
5. `Pause`, `Resume`, `Stop Selected` chỉ tác động luồng nuôi.

Bootstrap LD/ADB/Facebook được dùng chung với Auto Job qua `DeviceBootstrapService`, nên hai luồng không duy trì hai bản logic khởi động khác nhau.

## Xác thực Auto Job

1. Mở tab `Auto làm job`.
2. Nhập AuthToken vào ô masked.
3. Bấm `Xác thực`.
4. Tool gọi đúng contract có sẵn:
   - `verify_auth()`
   - `get_facebook_account_inventory()`
   - `get_publisher_server()`
5. Chỉ account active, không banned được hiển thị.

AuthToken không được ghi vào JSON, SQLite, log, tên screenshot hoặc exception. Khi token đổi, worker cũ được yêu cầu dừng, account/binding cũ bị xóa sau khi worker kết thúc và API session cũ được đóng.

## Binding LD với Facebook account

1. Bấm `Quét LDPlayer`.
2. Chọn chính xác một LDPlayer.
3. Chọn chính xác một Facebook account; combo không tự chọn account đầu tiên.
4. Xác nhận account đang đăng nhập đúng trên LD.
5. Bấm `Gán tài khoản cho LD`.

Không thể:

- gán cùng Facebook UID cho hai LD;
- chạy Nuôi tài khoản và Auto Job trên cùng LD cùng lúc;
- chạy hai Auto Job worker dùng cùng UID.

## Chạy Auto Job

1. Chọn max job, delay, cooldown, thời gian chờ mở job, số lần cuộn tìm Like và ngưỡng thử job thấp. Profile nhận dạng mặc định là ngang `540`, dọc `960`, `DPI 150`; ba giá trị này có thể chỉnh ngay trong nhóm Điều hướng target.
2. Chọn action được phép.
3. Bấm `Start Auto Job`.
4. Theo dõi worker hoặc log trong hai tab monitor ở cuối màn hình.
5. Dùng `Stop` để dừng interruptible sleep và đóng API session trong `finally`.

Tab Auto Job dùng splitter hai cột:

- bên trái là account và binding;
- bên phải là nhóm Công việc & Delay, Điều hướng target, Loại job và Chẩn đoán;
- Start/Pause/Resume/Stop luôn nằm ngay dưới cấu hình;
- worker và log dùng hai tab riêng, không cùng chiếm chiều cao.

Nút Start chỉ bật sau khi đã xác thực và có ít nhất một binding hợp lệ.

Mỗi worker mặc định pull bằng `low_job=0`. Khi checkbox `Cho phép thử job điểm
thấp` bật, sau 3 response rỗng liên tiếp worker thử `low_job=1` đúng một request
rồi lập tức quay về `low_job=0`; chu kỳ này lặp lại độc lập trên từng LD. Khi
checkbox tắt, mọi request luôn dùng `low_job=0`. Control `Thử sau số lần rỗng`
thay đổi ngưỡng fallback. Lỗi API/network không được tính là response rỗng.

Reaction mapping:

```text
API LIKE  -> like
API LOVE  -> love
API CARE  -> thuongthuong
API HAHA  -> haha
API WOW   -> wow
API SAD   -> sad
API ANGRY -> angry
```

Follow và Like Page có executor registry an toàn nhưng checkbox đang bị khóa với tooltip “Chưa có executor được xác minh”; tool không đoán tọa độ cho hai loại này.

## Điều hướng và nhận dạng

Auto Job không mở Facebook Home khi bootstrap. Sau khi LDPlayer/ADB boot ổn định và xác nhận package đã cài, worker force-stop Facebook đúng một lần để xóa task Home cũ; job đầu tiên được route trực tiếp.

`FacebookJobTargetResolver` chỉ nhận URL `http/https` thuộc Facebook, bỏ fragment/tracking, giữ các khóa identity như `story_fbid`, `id`, `fbid`, `v` và kiểm tra xung đột với `object_id`. Link `fb.me`, `fb.watch`, `/share/p`, `/share/r`, `/share/v` được resolve redirect tối đa 5 hop, timeout 8 giây, không gửi AuthToken/cookie. Redirect ra ngoài Facebook hoặc về Home/login bị từ chối. Mỗi job có tối đa hai candidate khác nhau, cùng identity: canonical trước, normalized original sau; không tự dựng URL từ `object_id`.

Trên chính LDPlayer, tool hỏi Package Manager handler cho ACTION_VIEW + URL. `-p` dưới đây chỉ là bộ lọc của lệnh query, không phải transport mở job:

```text
cmd package query-activities --brief --components --user 0
  -a android.intent.action.VIEW
  -c android.intent.category.BROWSABLE
  -d <canonical_url>
  -p com.facebook.katana
```

Launcher component bị loại động bằng query MAIN/LAUNCHER. Component VIEW được cache theo `adb_serial + Facebook versionCode + URL family`; khi Facebook cập nhật, cache tự mất hiệu lực. Tool không hardcode class Facebook.

Router mở đúng component được Package Manager trả về bằng danh sách argument, không dùng `shell=True`, browser, URI `fb://`, monkey hoặc Back:

```text
am start -W --user 0
  -n <com.facebook.katana/resolved_exported_view_activity>
  -a android.intent.action.VIEW
  -c android.intent.category.BROWSABLE
  -d <canonical_url>
  -f 0x34000000
```

`0x34000000` là NEW_TASK | CLEAR_TOP | SINGLE_TOP. Khi đã dùng `-n`, lệnh launch không thêm `-p`.

Mặc định:

```text
job_route_timeout_sec = 30.0
job_route_poll_interval_sec = 1.0
job_route_ready_stable_samples = 2
job_route_initial_grace_sec = 3.0
short_link_resolve_timeout_sec = 8.0
```

Probe ghi timeline `ROUTER_ACTIVITY -> LOADING -> TARGET_READY` hoặc `ROUTER_ACTIVITY -> RETURNED_HOME`. Target chỉ ready khi Facebook foreground, không còn ở router/gray screen, không ở Home, layout Feed/Reel đúng và ổn định ít nhất hai mẫu. `RETURNED_HOME` chỉ terminal sau khi đã quan sát route transition; baseline Home trong vài trăm mili giây đầu không đủ để kết luận.

Mỗi candidate chỉ launch một lần. Candidate thứ hai chỉ chạy khi URL thực sự khác và candidate trước return Home/timeout/command failure theo policy. Không gửi lại cùng URL, không force-stop giữa job, không pull job mới khi route chưa terminal và không vào locator/Submit trước `TARGET_READY`.

Like locator ưu tiên UI hierarchy và dùng chung bộ quy tắc ngữ nghĩa/bố cục trong
`UiTree`. Text hoặc counter không clickable được ánh xạ về clickable ancestor gần
nhất; ba nút Reaction/Comment/Share phải có vùng tap khác nhau. Feed matcher tự
xóa đường phân cách dài khỏi `like.png`, crop sát icon + chữ, lấy nhiều candidate
đa tỉ lệ rồi chỉ chấp nhận candidate thuộc cụm đầu của hàng Like/Bình luận/Chia sẻ.
Feed vẫn cuộn tối đa 3 lần theo mặc định khi bài dài. Reel tự nhận biết ảnh toàn
màn hình hay rail đã crop, bỏ counter khỏi template, crop sát thumb icon và chỉ
chấp nhận candidate là icon đầu của chuỗi action dọc có ít nhất hai icon hợp lệ
phía dưới. Contour chỉ xác minh bố cục, không tự sinh tọa độ tap.

Profile CV mặc định:

```text
screen_width_px = 540
screen_height_px = 960
screen_dpi = 150
```

DPI được dùng để quy đổi kích thước icon/gap kỳ vọng; vùng tìm kiếm vẫn dựa theo
tỷ lệ ảnh thực tế nên không hardcode tọa độ từ ảnh mẫu.

Trước tap, candidate được capture và tìm lại. Nếu tọa độ dịch quá tolerance, tool không tap.

## Submit và Report

- Submit chỉ khi `action_verified=True`.
- ADB return code 0 không được xem là bằng chứng đủ.
- Pixel thay đổi đơn lẻ không được xác nhận thành công; cần UI selected/checked hoặc hai quan sát liên tiếp có action layout ổn định và thay đổi cục bộ quanh nút.
- Mỗi job context chỉ có một terminal API action:
  - `SUBMITTED`
  - `REPORTED`
  - `SUBMIT_UNKNOWN`
  - `ABORTED_WITHOUT_API_ACTION`
- Không vừa Submit vừa Report cùng một job.
- Lỗi API/auth/network và thao tác Stop không bị Report.
- Lỗi thuộc job chỉ Report tối đa một lần sau khi hết candidate hợp lệ.

## Failure code

Các code chính:

```text
AUTH_INVALID
ACCOUNT_NOT_SELECTED
DEVICE_NOT_SELECTED
ACCOUNT_DEVICE_BINDING_MISSING
ADB_OFFLINE
FACEBOOK_NOT_INSTALLED
FACEBOOK_NOT_FOREGROUND
JOB_EMPTY
JOB_INVALID
JOB_LINK_MISSING
JOB_LINK_INVALID
JOB_TARGET_INVALID
JOB_LINK_REDIRECT_INVALID
UNSUPPORTED_JOB_ACTION
UNSUPPORTED_CONTENT_TYPE
LINK_NOT_OPENED
FACEBOOK_VIEW_HANDLER_NOT_FOUND
FACEBOOK_ROUTE_COMMAND_FAILED
FACEBOOK_ROUTE_COMPONENT_REJECTED
FACEBOOK_ROUTE_RETURNED_HOME
FACEBOOK_ROUTE_TIMEOUT
TARGET_NOT_VERIFIED
TARGET_LAYOUT_MISMATCH
LIKE_BUTTON_NOT_FOUND_AFTER_SCROLL
REEL_LIKE_BUTTON_NOT_FOUND
REACTION_PANEL_NOT_OPENED
REACTION_TARGET_NOT_FOUND
ACTION_COMMAND_FAILED
ACTION_NOT_CONFIRMED
REACTION_TYPE_MISMATCH
ACTION_RESULT_UNKNOWN
API_GET_FAILED
API_SUBMIT_FAILED
API_REPORT_FAILED
STOP_REQUESTED
```

## Log và evidence

- Log Auto Job nằm trong tab Auto Job.
- Evidence lỗi nằm tại `screenshots/job_failures/`.
- Navigation failure lưu ảnh đầu attempt, ảnh cuối và timeline JSON nhỏ khi tùy chọn được bật.
- Detector/action verification failure vẫn lưu ảnh cuối.
- Retention mặc định giữ tối đa 30 file cho mỗi loại evidence.
- Ảnh xử lý bình thường được capture trực tiếp vào memory bằng `adb exec-out screencap -p`.

Runtime artifact không thuộc release:

```text
data/*.sqlite3
logs/*.log
screenshots/**/*.png
__pycache__/
*.pyc
build/
dist/
*.spec
```

## Test

```powershell
python -m compileall -q .
python -m unittest discover -s tests -v
```

Test suite không gọi live API và không tự Submit. Live test phải được người vận hành xác nhận riêng.

## Build Windows

```powershell
.\scripts\build_windows.ps1
```

Nếu dependency và PyInstaller đã có:

```powershell
.\scripts\build_windows.ps1 -SkipInstall
```

Build dùng `--clean`, include `configs`, `resources` và package API từ repository root; không include database, logs, screenshots, tests hoặc diagnostics.

## Diagnostics

Probe thủ công được giữ tại `tools/diagnostics/` và không được đưa vào release:

- `adb_probe.py`
- `ldconsole_probe.py`
- `reaction_probe.py`
- `facebook_route_probe.py`

`reaction_probe.py` mặc định dry-run; chỉ tap khi truyền `--tap`.

Probe route live (không token/cookie, không tap, không Submit/Report):

```powershell
python tools/diagnostics/facebook_route_probe.py `
  --adb "D:\path\to\adb.exe" `
  --serial localhost:5557 `
  --url "https://www.facebook.com/..."
```

Probe in SDK/Facebook version, canonical URL, query command, component list, component được chọn, timing `am start -W`, timeline 10–15 giây, screenshot cuối và kết luận `TARGET_READY` hoặc `FACEBOOK_ROUTE_RETURNED_HOME`.

## Giới hạn còn lại

- Tool không thể đọc Facebook UID đang đăng nhập một cách đáng tin cậy trên mọi phiên bản Facebook, nên binding yêu cầu người dùng xác nhận.
- Detector phụ thuộc accessibility/layout thực tế; khi tín hiệu mâu thuẫn, tool fail-safe và không tap.
- Follow/Like Page chưa có UI flow đủ bằng chứng nên chưa thực thi.
- Không có live API/LDPlayer test trong test suite tự động.
