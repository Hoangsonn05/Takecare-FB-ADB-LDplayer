# Architecture

```text
MainWindow
  ├─ Nuôi tài khoản (legacy UI)
  │    └─ AutomationScheduler
  │         └─ FeedRunner per LD
  └─ Auto làm job (AutoJobTab)
       └─ JobAutomationController
            └─ JobAutomationScheduler
                 └─ JobRunner per explicit LD/account binding

FeedRunner ─┐
            ├─ DeviceBootstrapService
JobRunner  ─┘      ├─ LDConsoleManager
                   └─ ADBDevice -> CommandRunner

JobRunner
  ├─ GoLikeJobService -> canonical repository-root golike_api package
  ├─ FacebookJobRouter
  │    ├─ FacebookJobTargetResolver
  │    ├─ ADB Package Manager VIEW handler resolution
  │    └─ NavigationVerifier -> NavigationProbe
  ├─ ScreenClassifier
  └─ ExecutorRegistry
       ├─ ReactionExecutor
       │    ├─ UiTree -> shared semantic action-layout rules
       │    ├─ FeedLikeLocator -> horizontal action row + bounded scroll
       │    ├─ ReelLikeLocator -> vertical rail + Canny multi-scale matcher
       │    ├─ ReactionPanelLocator
       │    └─ ActionVerifier
       ├─ FollowExecutor (safe unsupported)
       └─ LikePageExecutor (safe unsupported)
```

Dependencies point inward toward core models/adapters. GUI does not contain API payload, ADB navigation or detector logic.

## Threading

- Qt main thread only renders widgets and drains queues.
- Auth/account load and LD scanning use `QThreadPool`.
- Nurture and Auto Job device workers use separate `ThreadPoolExecutor` instances.
- `GLOBAL_RESOURCE_LOCKS` prevents both modes from owning the same LD index.
- Every Auto Job worker creates and closes its own requests session.

## Auto Job state

```text
IDLE -> AUTHENTICATING -> AUTHENTICATED -> DEVICE_BOOTSTRAP
     -> GETTING_JOB -> VALIDATING_JOB -> NAVIGATING -> WAITING_TARGET
     -> VERIFYING_TARGET
     -> CLASSIFYING_SCREEN -> LOCATING_ACTION -> EXECUTING_ACTION
     -> VERIFYING_ACTION -> SUBMITTING -> COOLDOWN -> GETTING_JOB

job failure -> REPORTING -> COOLDOWN
system failure -> RECOVERING or ERROR
* -> STOPPING -> STOPPED
```

The state machine rejects invalid transitions. `JobExecutionContext` also rejects a second terminal API action.

GetJob luôn đi qua một contract `low_job`. Mỗi `JobRunner` giữ streak riêng: các
request thường gửi `low_job=0`; khi `low_job_fallback_enabled=True`, sau
`low_job_fallback_after_empty` response rỗng runner gửi `low_job=1` một lần rồi
reset về mode 0. API error không tăng streak. Khi checkbox tắt, streak được reset
và mọi request luôn dùng mode 0.

`FeedLikeLocator`, `ReelLikeLocator` và `ScreenClassifier` dùng chung
`UiTree.find_action_layout()`. Parser giữ quan hệ cha/con đủ để ánh xạ label hoặc
counter child về clickable ancestor gần nhất. UI hierarchy là bằng chứng mạnh; CV
contour chỉ là bằng chứng hỗ trợ và không được phép tạo điểm tap.

`TemplateMatcher` chịu trách nhiệm tiền xử lý và sinh top candidate đa tỉ lệ:
Feed xóa đường ngang dài rồi crop sát icon/label; Reel tách icon khỏi counter, crop
sát edge và tự chọn ROI toàn màn hình hoặc rail crop. Detector chịu trách nhiệm
structural validation: Feed yêu cầu ba cụm action ngang với candidate ở cụm đầu;
Reel yêu cầu candidate là icon đầu của chuỗi action dọc có hai icon phía dưới.

Profile mặc định 540x960, DPI 150 đi từ `JobAutomationConfig`/`RuntimeConfig` vào
matcher. Kích thước và khoảng cách icon được quy đổi theo DPI; tọa độ vẫn suy ra từ
ảnh hiện tại, không lấy từ ảnh mẫu.

## Facebook route session

Auto Job bootstrap dùng `launch_app=False`; luồng Nuôi tài khoản vẫn dùng `launch_app=True`. Worker Auto Job force-stop Facebook đúng một lần sau bootstrap và không mở launcher/Home trước khi lấy job.

```text
normalize/canonicalize target
  -> query exported Facebook VIEW component for exact URL
  -> explicit am start -n component
  -> poll ROUTER_ACTIVITY/LOADING/UNKNOWN
  -> require TARGET_READY N consecutive samples
  -> classify again
  -> locator/executor
```

Component cache key là `(adb_serial, facebook_versionCode, URL family)`. Launcher handler được query và loại động; không có class Facebook hardcode. Nếu không tìm thấy handler hoặc component bị SecurityException, flow dừng như lỗi hệ thống và không fallback sang implicit package/browser.

Probe kết hợp resolved/launched component, top/resumed `ActivitySnapshot`, top intent data, screenshot, UI XML, baseline difference và layout Feed/Reel. `RETURNED_HOME`, `WRONG_APP`, `BLOCKED` và `TERMINAL_ERROR` không bao giờ đi vào executor. Timeline chỉ kết luận `RETURNED_HOME` sau khi đã thấy router/loading transition.

Mỗi canonical/original candidate chỉ launch một lần, tối đa hai lệnh route/job. Không retry cùng URL, không Back, monkey, browser hoặc force-stop giữa candidate.

## Auto Job UI

`AutoJobTab` giữ controller/API ngoài GUI và chỉ tổ chức widget:

```text
auth box
vertical splitter
  horizontal splitter
    account + binding
    grouped job/navigation/action config + controls
  monitor tabs
    worker table
    log
```

Các widget trình bày nằm trong `gui/widgets/auto_job_panels.py` (`AuthPanel`, `AccountBindingPanel`, `JobConfigPanel`, `WorkerMonitorPanel`). Signal nút được nối ở `AutoJobTab`; widget con không gọi API hoặc ADB.

Auth và scan LD vẫn chạy trong `QThreadPool`; widget chỉ được cập nhật trên Qt main thread.
