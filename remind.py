# -*- coding: utf-8 -*-
"""今日の記録がまだなら促すリマインダ（Windows トースト／ターミナル表示）。

使い方:
  python3 remind.py --toast      systemd timer から。未記録ならトースト通知
  python3 remind.py --terminal   .bashrc から。未記録なら1行表示（記録済みなら無言）
  python3 remind.py --test       未記録かどうかに関わらず通知（動作確認用）

FastAPI に依存させない（標準ライブラリのみ）。venv を有効化していない
シェル起動時やタイマーからも同じスクリプトで呼べるようにするため。
"""

import base64
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

# .bashrc など任意の cwd から呼ばれるので、自分の場所を import パスに足す
sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402

URL = "http://127.0.0.1:8000"

# 朝は「今日の見積もり」、夜は「1日の振り返り」を促す。項目の性質が違うため。
MORNING = "朝の残MP見積もりと寝起きの良し悪しを記録しましょう。"
EVENING = "1日の振り返り（没頭タスク・散歩・一言メモ）を記録しましょう。"

# 非パッケージアプリからトーストを出すときは PowerShell の AppId を借りるのが通例
TOAST_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

# systemd user サービスの PATH には Windows 側のディレクトリが入らないため、
# PATH 解決に失敗したら既定の絶対パスも見る
POWERSHELL_CANDIDATES = (
    "powershell.exe",
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
)


def _body() -> str:
    return MORNING if datetime.now().hour < 12 else EVENING


def is_unrecorded(day: str) -> bool:
    return db.get_log(day) is None


def toast(title: str, body: str) -> bool:
    """WSL から Windows 側にトースト通知を出す。成功したら True。

    BurntToast 等のモジュールに依存しないよう WinRT API を直接叩く。
    日本語が Shift-JIS 解釈で壊れるのを避けるため -EncodedCommand（UTF-16LE）で渡す。
    """
    script = f"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null
$xml = @"
<toast activationType="protocol" launch="{URL}">
  <visual><binding template="ToastGeneric">
    <text>{title}</text>
    <text>{body}</text>
  </binding></visual>
</toast>
"@
$doc = [Windows.Data.Xml.Dom.XmlDocument]::new()
$doc.LoadXml($xml)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{TOAST_APP_ID}')
$notifier.Show([Windows.UI.Notifications.ToastNotification]::new($doc))
"""
    exe = next((p for p in (shutil.which(c) for c in POWERSHELL_CANDIDATES) if p), None)
    if exe is None:
        return False  # WSL 外など Windows 側を叩けない環境では黙って諦める

    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def terminal(day: str, body: str) -> None:
    """シェル起動時の1行表示。邪魔にならないよう記録済みなら何も出さない。"""
    color = sys.stdout.isatty()
    head = "\033[33m" if color else ""
    dim = "\033[2m" if color else ""
    off = "\033[0m" if color else ""
    print(f"{head}[MPログ] {day} の記録がまだです{off} {dim}{body} {URL}{off}")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "--terminal"
    today = date.today().isoformat()
    body = _body()

    if mode == "--test":
        return 0 if toast("MPログ（テスト）", "通知の動作確認です。") else 1

    if not is_unrecorded(today):
        return 0

    if mode == "--toast":
        return 0 if toast(f"MPログ: {today} の記録がまだです", body) else 1
    terminal(today, body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
