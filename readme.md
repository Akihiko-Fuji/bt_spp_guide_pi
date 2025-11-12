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
- パッケージ: bluez, bluez-tools, rfcomm (インストール手順は下記)

まずパッケージを更新・インストールします：

```bash
sudo apt update
sudo apt install -y bluez bluez-tools python3-serial
```

`python3-serial` は通信確認用 Python スクリプトで `pyserial` を利用するためのパッケージです。

---

## 3. BlueZ と SPP の基本理解

- **BlueZ** は Linux の Bluetooth スタック。`bluetoothd` がデーモン。
- **SPP (Serial Port Profile)** は RFCOMM を使用して仮想シリアルポート（`/dev/rfcomm0` 等）を提供するプロファイル。
- 近年の BlueZ では `serial` というプラグイン名は廃止され、代わりに `socket`（Socket プロファイル）を有効化することで RFCOMM/SPP 機能が利用可能。

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

### 5.2 SPP サービス登録（SDP へ追加）

```bash
sudo sdptool add SP
sudo systemctl restart bluetooth
sudo sdptool browse local | less
```

出力内に `Service Name: Serial Port` が表示され、`Protocol Descriptor List` に `RFCOMM` と `Channel` が見えればOK。

### 5.3 デバイスのペアリング（初回1回）

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
bt_auto_connect.py

シンプルで堅牢な rfcomm 自動再接続スクリプト
- rfcomm bind を試行し、失敗時は間隔をあけて再試行
- ログは /var/log/bt_auto_connect.log に出力
- systemd で管理する想定
"""

import subprocess
import time
import logging
import os
import random

# ----- 設定値 -----
BT_ADDR = "00:11:22:33:44:55"  # 実際のデバイスMACに置換
CHANNEL = "1"                  # sdptool で確認
RFCOMM_IDX = 0                  # /dev/rfcomm{RFCOMM_IDX}
RFCOMM_DEV = f"/dev/rfcomm{RFCOMM_IDX}"
RETRY_INTERVAL = 10             # 基本の待ち時間（秒）
MAX_RETRIES_BEFORE_BACKOFF = 6  # この回数で指数バックオフを開始
MAX_RETRIES = 0                 # 0 = 無制限
LOGFILE = "/var/log/bt_auto_connect.log"

# ----- ログ設定 -----
logging.basicConfig(
    filename=LOGFILE,
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)


def is_bound():
    """rfcomm が既にバインド済みか確認（RFCOMM_DEV が存在）"""
    return os.path.exists(RFCOMM_DEV)


def bind_rfcomm():
    """rfcomm bind を実行。成功 True/False を返す"""
    try:
        # 念のため一度 release をかける
        subprocess.run(["rfcomm", "release", str(RFCOMM_IDX)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    try:
        subprocess.run(["rfcomm", "bind", str(RFCOMM_IDX), BT_ADDR, CHANNEL], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logging.info(f"rfcomm bind succeeded: {RFCOMM_DEV}")
        return True
    except subprocess.CalledProcessError as e:
        logging.warning(f"rfcomm bind failed: {e}")
        return False


def release_rfcomm():
    try:
        subprocess.run(["rfcomm", "release", str(RFCOMM_IDX)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logging.info("rfcomm released (clean)")
    except Exception:
        pass


def auto_reconnect():
    retries = 0
    while True:
        if is_bound():
            # 既に接続済みなら少し待機
            logging.debug("Device appears bound; sleeping")
            time.sleep(5)
            continue

        logging.info("Device not bound; attempt to bind...")
        success = bind_rfcomm()
        if success:
            retries = 0
            # 余裕をもって接続後の安定時間を確保
            time.sleep(2)
            continue

        # 失敗した場合の待機とバックオフ
        retries += 1
        logging.warning(f"Bind attempt failed (#{retries})")

        if MAX_RETRIES > 0 and retries >= MAX_RETRIES:
            logging.error("Reached MAX_RETRIES; exiting")
            break

        # エクスポネンシャルバックオフ（ただし最大60秒）
        backoff = min(60, RETRY_INTERVAL * (2 ** max(0, (retries // MAX_RETRIES_BEFORE_BACKOFF))))
        # ジッタを入れて同時攻撃を避ける
        jitter = random.uniform(0, 2)
        sleep_time = backoff + jitter
        logging.info(f"Sleeping for {sleep_time:.1f}s before next retry")
        time.sleep(sleep_time)


if __name__ == '__main__':
    logging.info("Starting bt_auto_connect service")
    release_rfcomm()
    try:
        auto_reconnect()
    except KeyboardInterrupt:
        logging.info("bt_auto_connect interrupted by user")
        release_rfcomm()

```

### 解説（初心者向け）

- `rfcomm bind` に失敗しても即リトライせず、指数バックオフ＋ジッタで再試行するため**CPU暴走を防ぐ**。
- `/var/log/bt_auto_connect.log` に状態が残るのでデバッグが容易。
- `MAX_RETRIES=0` は無制限リトライ。必要なら上限を設定できます。

---

## 8. より堅牢な接続管理（改善版）

上記スクリプトをベースに、さらに以下の改善が可能：

- **ログローテーション**: `/etc/logrotate.d/bt_auto_connect` を作成してログ肥大を管理
- **非rootユーザーでの実行**: rfcomm を実行するには root 権限が必要だが、systemd の `User=root` を使わずに `Capability` を付与する方法もある（上級）
- **接続失敗時の通知**: systemd の `OnFailure=` や自前でメール送信／Slack通知
- **デバイスの電源管理との協調**: デバイスが電源断→再投入される場合のタイミング調整

---

## 9. systemd サービス化

### 9.1 単純なサービスファイル（推奨）

`/etc/systemd/system/bt-auto-connect.service` を作成します：

```ini
[Unit]
Description=Bluetooth SPP auto-connect
After=bluetooth.service
Requires=bluetooth.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/bt_auto_connect.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

有効化と起動：

```bash
sudo systemctl daemon-reload
sudo systemctl enable bt-auto-connect.service
sudo systemctl start bt-auto-connect.service
sudo journalctl -u bt-auto-connect -f
```

`Restart=always` と `RestartSec=5` により、スクリプトが落ちても自動で再起動されます。

### 9.2 rfcomm を systemd で直接 bind する One-shot サービス

この場合は `/usr/bin/rfcomm bind ...` を直接実行する oneshot サービスを作る方法もあります（接続が切れたときに自動で復活しない点に注意）。

---

## 10. イベントドリブン（D-Bus）方式のサンプル

ポーリングを避け、BlueZ の D-Bus イベントに反応して再接続する方法（CPU負荷がほぼゼロ）。

以下は概念的なサンプル（`dbus-next` や `pydbus` を使う想定）。

> 注意: 下記はサンプルコードであり、環境により調整が必要。依存ライブラリを事前にインストールしてください（例: `pip3 install dbus-next`）。

```python
# event_reconnect.py (概念サンプル)
import asyncio
from dbus_next.aio import MessageBus
from dbus_next.constants import MessageType

async def main():
    bus = await MessageBus().connect()
    introspect = await bus.introspect('org.bluez', '/')
    # ここで org.bluez のオブジェクトを監視し、Device の PropertiesChanged を subscribe
    # 切断イベントを受けたら rfcomm bind を行う処理を起動

if __name__ == '__main__':
    asyncio.run(main())
```

イベントドリブン実装はポーリングより確実かつ軽量ですが、やや難易度が高いので、まずはポーリング式で運用開始し、安定後に移行することを推奨します。

---

## 11. ログとトラブルシューティング

### 11.1 主要ログの場所

- BlueZ デーモン: `journalctl -u bluetooth` または `sudo journalctl -b | grep bluetooth`
- bt_auto_connect ログ: `/var/log/bt_auto_connect.log`
- systemd 単体: `journalctl -u bt-auto-connect`

### 11.2 よくある症状と対処

- **/dev/rfcomm0 が作成されない**
  - sdptool で `Serial Port` が登録されているか確認
  - rfcomm.conf の MAC と Channel を再確認
  - `rfcomm bind` を手動で実行し、エラーを確認

- **接続試行が短時間に何度も発生して SSH が切れる**
  - スクリプトの `time.sleep()` を長めに設定（例: 10〜30秒）
  - systemd の `RestartSec` を調整

- **ペアリングがうまくいかない**
  - `bluetoothctl` で `agent on` と `default-agent` を使い、手動で一度ペアリング
  - PIN 認証がある場合は適切に入力

---

## 12. FAQ

**Q1: --compat を付けたほうがいいですか？**
A1: 2025年現在の一般的な Raspberry Pi OS では不要。状況に応じて使われることもあるが、まずは `sdptool add SP` と `Enable=Socket` で試す。

**Q2: rfcomm.conf と rfcomm bind、どちらが良い？**
A2: rfcomm.conf は便利だが BlueZ のバージョンによっては確実に動かないことがある。確実性を求めるなら systemd の ExecStart で `rfcomm bind` を明示実行するのが良い。

**Q3: 接続試行中に SSH が切断される原因は？**
A3: スクリプトが短い間隔で無限ループして `rfcomm` をひっきりなしに呼ぶことが原因。sleep とバックオフで解決する。

---

## 13. 付録：ファイル一覧（作成・編集推奨）

- `/etc/bluetooth/main.conf` (編集)
- `/etc/bluetooth/rfcomm.conf` (作成)
- `/usr/local/bin/bt_auto_connect.py` (作成)
- `/etc/systemd/system/bt-auto-connect.service` (作成)
- (任意) `/usr/local/bin/event_reconnect.py` (D-Bus 実装)

---

**作成日:** 2025-11-12

**作成者:** ChatGPT（GPT-5 Thinking mini） とAkihiko Fujiとの会話に基づく


---

# 末尾メモ

必要であれば、上記のファイルを個別に生成して配置するためのシェルスクリプトも作成します。希望があればそのまま用意します。

