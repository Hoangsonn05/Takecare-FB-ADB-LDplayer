# Changelog

## v4.7 - Scoped reaction resolution and exact verification

- Added anchor identity with Post/Reel action-cluster bounds, scope signature and frame evidence.
- Added scoped reaction pre-check; an already-active reaction is not tapped again.
- Removed fixed Feed/Reel reaction-option ordering.
- Bound reaction panels to the long-pressed anchor using before/after UI and visual evidence.
- Required the verified active reaction to equal the requested reaction.
- Added `REACTION_TYPE_MISMATCH` and failure-only reaction diagnostic bundles.
- Added focused core regression tests for scope, panel, pre-check and exact verification.

## v4.6 - Fix Like locator 540x960 DPI 150

- Làm sạch template Feed: loại đường ngang dài, chọn polarity foreground và crop sát icon + nhãn.
- Matcher trả nhiều candidate đa tỉ lệ; Feed/Reel detector mới quyết định tọa độ bằng bố cục.
- Feed chỉ chấp nhận candidate đầu của hàng Like/Bình luận/Chia sẻ.
- Reel tự nhận biết rail crop, bỏ counter, crop sát thumb icon và yêu cầu chuỗi action dọc.
- UI hierarchy ánh xạ text/counter child về clickable ancestor gần nhất.
- Action verifier không còn xác nhận chỉ vì pixel đổi; cần selected/checked hoặc hai frame có layout ổn định và thay đổi cục bộ.
- Thêm profile màn hình mặc định 540x960, DPI 150 vào RuntimeConfig và Auto Job UI.
- Thêm regression tests từ hai ảnh Feed/Reel thực tế.

## v3

- Removed the standalone Validate Paths button from GUI.
- Facebook package is fixed to `com.facebook.katana`.
- Added robust LDPlayer base auto-resolution.
- Manual LD IDs can start even when `ldconsole list2` returns no instances.
- Worker now logs precise path, launch, ADB and app-opening stages.
- Added ADB monkey fallback for opening Facebook when `ldconsole runapp` fails.

## v2

- Added hardcoded Facebook package.
- Added LD IDs input and Start IDs.
- Added basic already-running check.

## v1

- Initial modular GUI/core/scheduler project.
