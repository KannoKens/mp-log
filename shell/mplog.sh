# -*- sh -*-
# MPログのシェル導線。.bashrc から source して使う（install.sh が1行だけ追記する）。

MPLOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MPLOG_URL="http://127.0.0.1:8000"

# `mplog` で入力画面を開く。常駐サービスが落ちていても起こしてから開く。
mplog() {
    if ! systemctl --user is-active --quiet mplog.service 2>/dev/null; then
        systemctl --user start mplog.service 2>/dev/null
    fi
    if command -v wslview >/dev/null 2>&1; then
        wslview "$MPLOG_URL"
    else
        explorer.exe "$MPLOG_URL" 2>/dev/null || true  # explorer.exe は成功時も非0を返すことがある
    fi
}

# 対話シェルを開いたとき、今日の記録がまだなら1行だけ促す（記録済みなら無言）
case $- in
    *i*) python3 "$MPLOG_DIR/remind.py" --terminal 2>/dev/null ;;
esac
