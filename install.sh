#!/usr/bin/env bash
# MPログの「日々使う導線」をセットアップする（何度実行しても安全）。
#
#   1. systemd user サービスで uvicorn を常駐させる（WSL 起動時に自動で立つ）
#   2. 朝夜のリマインドタイマーを仕込む（未記録の日だけ Windows にトースト通知）
#   3. .bashrc に1行だけ追記し、`mplog` コマンドと起動時リマインドを有効化する
#   4. Claude Code のスキル（skills/）を ~/.claude/skills/ からリンクする
#
# 解除は ./install.sh --uninstall

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
SKILL_DIR="$HOME/.claude/skills"
SKILLS="today session-close"
BASHRC="$HOME/.bashrc"
MARKER_BEGIN="# >>> mp-log >>>"
MARKER_END="# <<< mp-log <<<"

info() { printf '  %s\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }

uninstall() {
    systemctl --user disable --now mplog.service mplog-remind.timer 2>/dev/null || true
    rm -f "$UNIT_DIR/mplog.service" "$UNIT_DIR/mplog-remind.service" "$UNIT_DIR/mplog-remind.timer"
    systemctl --user daemon-reload
    ok "systemd ユニットを削除しました"

    if grep -qF "$MARKER_BEGIN" "$BASHRC" 2>/dev/null; then
        # マーカー行の間だけを削除する
        sed -i "/$MARKER_BEGIN/,/$MARKER_END/d" "$BASHRC"
        ok ".bashrc の設定を削除しました"
    fi

    # このリポジトリを指しているリンクだけを消す。実体のディレクトリや
    # 別の場所を指すリンクは、こちらが作ったものではないので触らない
    for skill in $SKILLS; do
        dst="$SKILL_DIR/$skill"
        if [ -L "$dst" ] && [ "$(readlink -f "$dst")" = "$REPO_DIR/skills/$skill" ]; then
            rm -f "$dst"
            ok "スキル $skill のリンクを削除しました"
        fi
    done
    exit 0
}

[ "${1:-}" = "--uninstall" ] && uninstall

echo "MPログ 導線セットアップ ($REPO_DIR)"

# --- 前提チェック ---------------------------------------------------------
if [ ! -x "$REPO_DIR/.venv/bin/uvicorn" ]; then
    warn ".venv/bin/uvicorn が見つかりません。先に依存をインストールしてください:"
    info "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi
if ! systemctl --user show-environment >/dev/null 2>&1; then
    warn "systemd の user インスタンスが使えません（WSL の systemd 有効化が必要）"
    exit 1
fi

# --- 1. systemd ユニットを配置 --------------------------------------------
mkdir -p "$UNIT_DIR"
for unit in mplog.service mplog-remind.service mplog-remind.timer; do
    if [ "$REPO_DIR" = "$HOME/mp-log" ]; then
        # 標準の場所。ユニット内の %h で解決できるので symlink で十分
        # （リポジトリ側を編集したら daemon-reload だけで反映される）
        ln -sf "$REPO_DIR/systemd/$unit" "$UNIT_DIR/$unit"
    else
        # 別の場所に置いている場合は %h/mp-log を実パスに置換して複製する
        sed "s|%h/mp-log|$REPO_DIR|g" "$REPO_DIR/systemd/$unit" > "$UNIT_DIR/$unit"
    fi
done
systemctl --user daemon-reload
ok "systemd ユニットを配置しました ($UNIT_DIR)"

# --- 2. 常駐サーバとタイマーを有効化 --------------------------------------
systemctl --user enable --now mplog.service >/dev/null 2>&1
systemctl --user enable --now mplog-remind.timer >/dev/null 2>&1
ok "常駐サーバとリマインドタイマーを有効化しました"

# ログインしていない間もサービスを動かすため（失敗しても致命的ではない）
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    if loginctl enable-linger "$USER" 2>/dev/null; then
        ok "linger を有効化しました（ログイン前から常駐）"
    else
        warn "linger の有効化に失敗（権限不足）。必要なら: sudo loginctl enable-linger $USER"
    fi
fi

# --- 3. .bashrc に導線を追加 ----------------------------------------------
if grep -qF "$MARKER_BEGIN" "$BASHRC" 2>/dev/null; then
    ok ".bashrc は設定済みです"
else
    cat >> "$BASHRC" <<EOF

$MARKER_BEGIN
[ -f "$REPO_DIR/shell/mplog.sh" ] && . "$REPO_DIR/shell/mplog.sh"
$MARKER_END
EOF
    ok ".bashrc に追記しました（次のシェルから有効）"
fi

# --- 4. Claude Code のスキルをリンク --------------------------------------
# スキルの実体はこのリポジトリで管理し、~/.claude/skills/ からは symlink で
# 参照する（~/.claude は git 管理外なので、実体を置くと版が残らない）。
# skills/ は公開リポジトリには含めていないので、無ければこの節ごと飛ばす。
mkdir -p "$SKILL_DIR"
linked=0
for skill in $SKILLS; do
    src="$REPO_DIR/skills/$skill"
    dst="$SKILL_DIR/$skill"
    [ -d "$src" ] || continue
    if [ -e "$dst" ] && [ ! -L "$dst" ]; then
        warn "$dst は実体のディレクトリです。退避してから再実行してください（リンクを張っていません）"
        continue
    fi
    # -n を付けないと、既存のリンク先ディレクトリの「中」にリンクを作ってしまう
    ln -sfn "$src" "$dst"
    linked=$((linked + 1))
done
if [ "$linked" -gt 0 ]; then
    ok "Claude Code のスキルをリンクしました ($SKILL_DIR)"
else
    info "skills/ が無いのでスキルのリンクは省略しました"
fi

echo
ok "完了。使い方:"
info "mplog                                  入力画面をブラウザで開く"
info "systemctl --user status mplog          サーバの状態を見る"
info "systemctl --user list-timers 'mplog*'  次のリマインド時刻を確認"
info "python3 remind.py --test               通知の動作確認"
info "./install.sh --uninstall               導線を解除"
