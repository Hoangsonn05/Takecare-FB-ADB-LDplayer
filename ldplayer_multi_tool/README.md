# LDPlayer Facebook Feed Tool v3

PySide6 GUI de ket noi mot hoac nhieu LDPlayer theo index, mo Facebook Android package co dinh, cuon feed, va tuy chon tha reaction bang template matching.

## Cach chay

```bat
pip install -r requirements.txt
python app.py
```

Hoac double-click:

```bat
run_gui.bat
```

## Package Facebook

Package duoc hardcode va khong can nhap tren GUI:

```text
com.facebook.katana
```

## LDPlayer base

Mac dinh:

```text
D:\MMO\QuanlyLDPlayer\LD9Mod-32bit\LD9Mod
```

Thu muc dung phai chua:

```text
ldconsole.exe
ld.exe
adb.exe
```

## Template folder

Mac dinh:

```text
resources/templates/
```

Can co cac file:

```text
like.png
love.jpg
thuongthuong.jpg
haha.jpg
wow.jpg
sad.jpg
angry.jpg
```

`like.png` duoc dung de tim nut Like. Neu reaction duoc chon la `Like`, tool tap truc tiep vao nut Like. Cac reaction khac se long press nut Like, doi panel reaction mo, roi match icon trong panel.

## Run Config

- `So post = 0` nghia la khong gioi han theo post.
- `So phut chay` van la gioi han thoi gian toi da.
- `So cam xuc toi da` la gioi han tong reaction cho moi LD trong mot lan chay.
- `Ti le tha cam xuc (%)` la xac suat thu reaction o swipe dau tien cua moi post.
- 7 checkbox cam xuc la cac reaction duoc phep chon: Like, Love, Thuong thuong, Ha ha, Wow, Sad, Angry.

Neu `Bat tha cam xuc` dang tat, app khong yeu cau folder template phai co anh va van chay scroll binh thuong.

## Cach dung nhanh

1. Mo `python app.py` hoac `run_gui.bat`.
2. Kiem tra `LDPlayer base`.
3. Bam `Scan` de doc danh sach LDPlayer tu `ldconsole list2`.
4. Tick cac LD muon chay o cot `Use`.
5. Chinh `Run Config` neu can, bam `Save Config` de luu vao `configs/default.json`.
6. Bam `Start Selected`.

Chay nhanh theo ID thu cong:

1. Nhap LD IDs, vi du `4` hoac `0,1,4`.
2. Bam `Add IDs`; dong da co trong bang se khong bi tao trung.
3. Bam `Start IDs`.

Tool se launch LDPlayer neu can, connect ADB theo `localhost:5555 + 2*index`, mo Facebook bang `com.facebook.katana`, roi chay vong lap theo runtime config tren GUI.

`worker_start_stagger_sec` trong `configs/default.json` quy dinh so giay cho giua moi worker khi start nhieu LD cung luc. Mac dinh la `5` de tranh bootstrap nhieu LD cung thoi diem gay lag may.

## Startup flow

Khi bam `Start IDs` hoac `Start Selected`, moi worker se chay bootstrap rieng cho LD index cua no:

1. Resolve `LDPlayer base`.
2. Kiem tra du `ldconsole.exe`, `ld.exe`, `adb.exe`.
3. Tinh ADB serial theo cong thuc `localhost:5555 + 2*index`.
4. Connect ADB va doc `adb -s <serial> get-state`.
5. Kiem tra LDPlayer da chay chua bang `ldconsole list2`, fallback bang ADB state.
6. Neu LD chua chay, goi `ldconsole.exe launch --index=<index>`.
7. Poll ADB moi 2 giay cho den khi device san sang.
8. Poll `getprop sys.boot_completed` cho den khi Android boot xong.
9. Cho settle theo `ld_settle_sec`.
10. Mo Facebook bang `ldconsole runapp --packagename=com.facebook.katana`; neu loi thi fallback ADB monkey.
11. Poll foreground app bang `dumpsys window` hoac `dumpsys activity activities`.
12. Cho settle theo `facebook_settle_sec`.
13. Chi sau do moi bat dau runtime config va feed loop.

Tool chi bat dau run config sau khi `com.facebook.katana` duoc xac nhan nam trong foreground activity hien tai. Package xuat hien trong recent task, service, cached process, hoac output dumpsys khac khong duoc tinh la san sang.

Status GUI theo thu tu: `STARTING`, `CHECKING_PATHS`, `CONNECTING_ADB`, `LAUNCHING`, `WAITING_BOOT`, `OPENING_APP`, `WAITING_FACEBOOK`, `RUNNING`, `STOPPING`, `STOPPED`, `ERROR`.

Neu mot buoc bootstrap loi, chi worker cua LD do chuyen `ERROR`; GUI va cac LD khac van tiep tuc chay.

## Luu y ve duong dan data/config

Ban nay resolve tat ca duong dan tuong doi theo thu muc tool, khong theo thu muc terminal dang dung. Vi vay cac file runtime se luu dung vao:

```text
configs/default.json
data/runs.sqlite3
logs/
screenshots/
resources/templates/
```

Neu chay `python D:\...\ldplayer_multi_tool\app.py` tu thu muc khac, app van tu dong chuyen working directory ve thu muc tool de tranh tao nham `configs/` hoac `data/` o ben ngoai.

## Scan LDMultiplayer

Nut `Scan` se thu theo thu tu:

1. `ldconsole.exe list2`
2. `ldconsole.exe list`
3. fallback quet cac thu muc `vms` thuong gap cua LDPlayer de tim profile da tao trong LDMultiplayer, bao gom ca profile dang tat.

Ket qua duoc merge theo `Index`. Neu `list2` khong tra ve du profile tren mot ban LDPlayer cu the, fallback filesystem van co the bo sung cac index/profile dang ton tai de nguoi dung tick chay.
