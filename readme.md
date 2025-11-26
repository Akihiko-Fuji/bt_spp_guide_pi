# Raspberry Pi Bluetooth SPP 完全ガイド（2025年版）

**目的**

このドキュメントは、Raspberry Pi OS（Bookworm / Bullseye）上で Bluetooth SPP (Serial Port Profile) を安定して運用するための、初心者向け手順書です。起動時の自動接続、自動ペアリング、`/dev/rfcomm0` の扱い、切断時の再接続（ポーリング式とイベントドリブン式）まで記載しています。

---

## 目次

1. 概要
2. 前提条件と準備
3. BlueZ と SPP の基本理解
4. 重要な注意点（`--compat` 等）
5. 実践：手順（コマンド集）
6. `rfcomm.conf` と `main.conf` の設定例
7. Python: ポーリング式自動再接続（`bt_auto_connect.py`）
8. Python: より堅牢な接続管理（改善版）
9. systemd サービス化
10. イベントドリブン（D-Bus）方式のサンプル
11. ログとトラブルシューティング
12. よくある質問（FAQ）
13. 付録：コマンド・ファイル一覧

---

## 1. 概要
WindowsでBluetoothを扱うのは比較的容易で、ペアリングさえおこなえば利用可能となるのですが、Raspberry Pi OS (DebianベースのLinux)では、Bluetoothに対するSPP接続のためのドライバ(プロトコルスタック)が有効化されていないため、ペアリングしても動作させることが出来ない（経験上、プロトコルスタックを有効にしても適切に機能しません）。また、Windowsのように起動時にオートコネクトする機能や切断時に自動で再接続する機能が、起動後に手動でペアリングとコネクトをおこなわないとデバイスを利用することが出来ない仕様となっている。

Raspberry Pi で外付けまたは内蔵Bluetoothデバイス（例：HC-06、産業用SPP機器、バーコードスキャナ）と SPP 通信を行うときに必要な設定と運用方法をまとめる。特に以下を満たすことを目的とする：

- 起動時に自動で接続（自動バインド）する
- 切断時に自動再接続する（CPU負荷を低く保つ）
- SSH や他のサービスに影響を与えない
- 初心者でも再現できる手順と説明

---

## 2. 前提条件と準備

- Raspberry Pi（Pi 4 / Zero 2W 等）
- Raspberry Pi OS (Bullseye / Bookworm 等、2025年時点の一般的な版)
- root または sudo 権限を持つユーザー
- Python 3 (推奨: 3.8+)
- パッケージ: bluez, bluez-tools, blueman (インストール手順は下記)
- どうもBlueZでのSPP通信はうまくいかないので、bluemanを基軸に設定する

まずパッケージを更新・インストールします：

```bash
sudo apt update
sudo apt install -y bluez bluez-tools python3-serial blueman 
```

`python3-serial` は通信確認用 Python スクリプトで `pyserial` を利用するためのパッケージです。

---

## 3. BlueZ と SPP の基本理解

- **BlueZ** は Linux の Bluetooth スタック。`bluetoothd` がデーモン。
- **SPP (Serial Port Profile)** は RFCOMM を使用して仮想シリアルポート（`/dev/rfcomm0` 等）を提供するプロファイル。
- 近年の BlueZ では `serial` というプラグイン名は廃止され、代わりに `socket`（Socket プロファイル）を有効化することで RFCOMM/SPP 機能が利用可能。

blueman導入後にはXwindow上のタブにマネージャーのアイコンが追加されます。機能が重複しますので古いBluetoothの管理アイコンは消しておくのが適切です。
インストール後に、bluemanから、リーダーとペアリングを行ない、シリアル通信の自動接続のチェックボックスを入れて下さい（チェックボックスを入れても、残念ながらそれだけでは自動接続が出来ません）ペアリング実施後にはファイルマネージャ(PCmanFM)から /dev/rfcomm* が出現していることを確認してください。

重要: `bluetoothd --compat` は古いケースで使われていたが、現行ディストロの BlueZ では**不要・場合によっては問題**になる。詳しくはセクション4。

---

## 4. 重要な注意点（`--compat` 等）

- 古いチュートリアルでは `ExecStart=/usr/lib/bluetooth/bluetoothd --compat` のように `--compat` を推奨する記述がある。
- しかし Bookworm 等の環境では `bluetoothd` の実行パスが `/usr/libexec/bluetooth/bluetoothd` になっていたり、`--compat` が D-Bus の新しい動作と競合し問題を引き起こすことがある。
- したがって **systemd unit の直接書き換えで `--compat` を付けることは推奨しない**。

代替案：
- `sdptool add SP` で SPP サービスを SDP に追加する（`--compat` 不要）。
- `main.conf` の `Enable=Socket` を確認する。

---

## 5. 実践：手順（コマンド集）

### 5.1 BlueZ の起動確認

```bash
systemctl status bluetooth
bluetoothd -v  # BlueZ のバージョン確認
```
### Bluetooth SPP接続処理の優先度を高める
dmesgを利用して標準のセットアップ状態での起動処理のロード時間見ると、全モジュールが20秒以内でロードされているのに対し、Bluetooth RFCOMM TTYのロードに40秒ほど掛かっているのが正常です(Pi Zero 2Wの場合)よりトラブル無く動作させるために、ドライバのロードの優先度を変更します。
 /etc/modules-load.d/modules.conf に、ロードの優先度を高めるため編集します
```bash
sudo nano /etc/modules-load.d/modules.conf
```
追記内容は下記の情報をi2c-devの上の行に追記します。
```bash
rfcomm
```
これにより、呼び出しの優先度が高くなり、起動後早い時点でモジュールがロードされるようになる為、以降の処理でモジュールがロードされておらず上手く動かないという障害を避けることが出来ます。

### 5.3 SPP サービス登録（SDP へ追加）

```bash
sudo sdptool add SP
sudo systemctl restart bluetooth
sudo sdptool browse local | less
```

出力内に `Service Name: Serial Port` が表示され、`Protocol Descriptor List` に `RFCOMM` と `Channel` が見えればOK。

### 5.4 デバイスのペアリング（初回1回）

```bash
bluetoothctl
# 対話内で
power on
agent on
default-agent
scan on  # 必要に応じて
pair XX:XX:XX:XX:XX:XX
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
```

※ XX:... は接続対象の MAC アドレス。

### 5.4 rfcomm の手動バインドテスト

まずチャンネルを調べる：

```bash
sudo sdptool browse XX:XX:XX:XX:XX:XX
```

出力の `Channel: N` を確認してから：

```bash
sudo rfcomm bind 0 XX:XX:XX:XX:XX:XX N
ls -l /dev/rfcomm0
```

`/dev/rfcomm0` が存在すれば成功。

---

## 6. 設定ファイル例

### 6.1 `/etc/bluetooth/main.conf`

```ini
[General]
Enable=Source,Sink,Media,Socket

# その他必要に応じて
# AutoEnable=true
```

※ `Serial` は不要（廃止）。

### 6.2 `/etc/bluetooth/rfcomm.conf`（自動バインド用）

```ini
rfcomm0 {
    bind yes;
    device 00:11:22:33:44:55;  # 実際のMACに置換
    channel 1;                 # sdptool で確認したチャンネル
    comment "SPP Device";
}
```

`rfcomm bind all` で起動時にバインドできますが、BlueZバージョンによってはうまく動かないことがあるため **systemd による `rfcomm bind` の明示実行** を推奨します（次セクション）。

---

## 7. ポーリング式自動再接続（`bt_auto_connect.py`）

以下はあなたが以前使っていた設計思想を元に、**再接続間隔制御・無限アタック回避・ログ出力**を組み合わせた実用的なスクリプトです。

保存先例: `/usr/local/bin/bt_auto_connect.py`

```python
#!/usr/bin/env python3
"""
bt_auto_connect.py - Blueman 相当の自動接続スクリプト

機能:
- 自動ペアリング
- SPP チャンネル自動検出
- rfcomm0 自動作成
- 再接続ループ (指数バックオフ + ジッタ)
- ログ: /tmp/bt_auto_connect.log
- sudo pip3 install dbus-next --break-system-packages しておいてください。
"""

import asyncio
import logging
import os
import random
import subprocess
import sys
from dbus_next.aio import MessageBus
from dbus_next.constants import BusType
from dbus_next.errors import DBusError

# ----- 設定 -----
BT_ADDR = "AA:A8:02:04:D3:A1" # MACアドレスは機器に合わせて設定
RFCOMM_DEV = "/dev/rfcomm0"
LOGFILE = "/tmp/bt_auto_connect.log"
RETRY_INTERVAL = 10           # 秒
MAX_RETRIES_BEFORE_BACKOFF = 6
MAX_RETRIES = 0               # 0 = 無制限

# ログ設定
logging.basicConfig(
    filename=LOGFILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# SDP から SPP チャンネルを自動検出
async def get_spp_channel(bus, device_path):
    try:
        obj = await bus.get_proxy_object('org.bluez', device_path, None)
        services = await obj.call_get_managed_objects()
        # RFCOMM チャンネルを返す簡易処理
        for path, ifaces in services.items():
            for iface, props in ifaces.items():
                if 'org.bluez.SerialPort' in iface or 'org.bluez.Rfcomm' in iface:
                    return props.get('Channel', 1)
        # デフォルト 1 チャンネル
        return 1
    except Exception as e:
        logging.warning(f"SPP channel autodetect failed: {e}")
        return 1

# ペアリングして trusted にする
async def ensure_paired(bus):
    device_path = f"/org/bluez/hci0/dev_{BT_ADDR.replace(':','_')}"
    try:
        obj = await bus.get_proxy_object('org.bluez', device_path, None)
        props_iface = obj.get_interface('org.freedesktop.DBus.Properties')
        paired = await props_iface.get('org.bluez.Device1', 'Paired')
        if not paired:
            logging.info("Device not paired. Attempting pair...")
            device = obj.get_interface('org.bluez.Device1')
            await device.Pair()
            logging.info("Paired successfully")
        await props_iface.set('org.bluez.Device1', 'Trusted', True)
        logging.info("Device set as trusted")
    except DBusError as e:
        logging.warning(f"ensure_paired DBusError: {e}")
    except Exception as e:
        logging.warning(f"ensure_paired error: {e}")
    return device_path

# rfcomm bind を実行
def bind_rfcomm(channel):
    try:
        subprocess.run(['rfcomm', 'release', '0'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        subprocess.run(['sudo', 'rfcomm', 'bind', '0', BT_ADDR, str(channel)], check=True)
        logging.info(f"rfcomm bind succeeded: {RFCOMM_DEV}")
        return True
    except subprocess.CalledProcessError as e:
        logging.warning(f"rfcomm bind failed: {e}")
        return False

async def auto_reconnect_loop():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    retries = 0
    while True:
        device_path = await ensure_paired(bus)
        channel = await get_spp_channel(bus, device_path)
        if os.path.exists(RFCOMM_DEV):
            # 接続済みなら待機
            await asyncio.sleep(5)
            continue
        logging.info(f"Attempting rfcomm bind on channel {channel}")
        success = bind_rfcomm(channel)
        if success:
            retries = 0
            await asyncio.sleep(2)
            continue
        # 失敗時バックオフ
        retries += 1
        if MAX_RETRIES > 0 and retries >= MAX_RETRIES:
            logging.error("Reached MAX_RETRIES; exiting")
            break
        backoff = min(60, RETRY_INTERVAL * (2 ** max(0, retries // MAX_RETRIES_BEFORE_BACKOFF)))
        jitter = random.uniform(0, 2)
        sleep_time = backoff + jitter
        logging.info(f"Sleeping {sleep_time:.1f}s before retry")
        await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    logging.info("Starting bt_auto_connect service")
    try:
        asyncio.run(auto_reconnect_loop())
    except KeyboardInterrupt:
        logging.info("bt_auto_connect interrupted by user")


```

### 解説（初心者向け）
- `MAX_RETRIES=0` は無制限リトライ。必要なら上限を設定できます。

---


---

## 9. systemd サービス化

### 9.1 単純なサービスファイル（推奨）

`/etc/systemd/system/bt-auto-connect.service` を作成します：

```ini
[Unit]
Description=Auto Bluetooth SPP Connect Service
After=bluetooth.target
Requires=bluetooth.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/bt_auto_connect.py
Restart=always
RestartSec=10
User=root
# rfcomm0 作成のため root 必須

[Install]
WantedBy=multi-user.target
```

有効化と起動：

```bash
sudo systemctl daemon-reload
sudo systemctl enable bt-auto-connect.service
sudo systemctl start bt-auto-connect.service

```

---

**作成日:** 2025-11-13

**作成者:** Akihiko Fuji



