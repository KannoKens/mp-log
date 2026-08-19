# -*- coding: utf-8 -*-
"""MPログの永続化層（SQLite）。

最小構成のため標準ライブラリの sqlite3 を直接使う。
日本語を扱うため、文字列は全て UTF-8 前提で扱う（sqlite3 は Python3 では
str をそのまま Unicode として保存するので明示のエンコード指定は不要だが、
外部ファイル入出力を足すときは encoding='utf-8' を必ず付けること）。
"""

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "mplog.db"

# mp_level の語彙。DBには key の文字列のまま保存し、value（代表値）は
# カレンダーの色分けや集計など数値が要る場面でのみ使う（SPEC.md参照）
MP_LEVELS: dict[str, dict] = {
    "excellent": {"label": "絶好調", "value": 17},
    "safe":      {"label": "安全圏", "value": 10},
    "caution":   {"label": "やや注意", "value": 7},
    "danger":    {"label": "危険", "value": 3},
}

WAKE_QUALITIES: dict[str, str] = {"good": "良い", "normal": "普通", "bad": "悪い"}

# /today が候補に付ける「見積もり」の語彙（MP1/MP3/MP5）。見積もりと実測を
# 同じ物差しで並べて較正するのが目的なので、ここは勝手に増やさない。
# 増やすと過去の見積もりと比較できなくなる。
MP_COSTS: dict[int, str] = {
    1: "軽い（15分程度・機械的）",
    3: "腰を据える（判断を伴う）",
    5: "消耗が大きい（対人・面接・MTG）",
}

# 「実測」(activities.mp_cost) は見積もりより細かくてよい。3段階に丸めると
# 「MP3と見積もった作業は実際はやや軽い」というズレが原理的に見えなくなり、
# 較正という目的そのものが損なわれる。個人のMP消費表（家事=2 など）を
# そのまま書ける幅にしてある。
# MP_LEVELS の value（3〜17）は「その日の残MP」の代表値で、こちらは「消費量」。
# 尺度は揃えてあるので、1日の消費合計と mp_level の代表値は同じ軸で比べてよい。
MP_COST_MIN, MP_COST_MAX = 1, 5

# 活動タグ → 消費MP。Web入力のタグから活動行を起こすときに引く表。
# 個人ごとに違うので config.py へ外出しする（.gitignore 済み）。
# ⚠ 消費量が「読めない」活動（没頭しやすいプログラミング等）は載せないこと。
# 固定値を置くと、実態とズレたまま /today の判断材料になる。
try:
    from config import TAG_MP_COSTS  # 個人設定（.gitignore 済み・公開しない）
except ImportError:
    TAG_MP_COSTS: dict[str, int] = {}

# 集計でこの件数未満のグループは「参考値」扱いにする。数日分の平均を
# 傾向として断定すると、体調の判断材料としてかえって有害なため。
MIN_SAMPLES = 5

# 週次サマリで「その週の平均」として扱うのに必要な最低記録日数
MIN_WEEK_DAYS = 3

# activity_tags の固定語彙。個人ごとに変わるので config.py（.gitignore 済み）へ外出しする。
# config.py が無ければ汎用サンプルで動く（自分用は config.example.py をコピーして編集）。
try:
    from config import ACTIVITY_TAGS  # 個人設定（.gitignore 済み・公開しない）
except ImportError:
    ACTIVITY_TAGS = ["仕事", "勉強", "運動", "家事", "趣味", "外出"]

# 体調・状態はチェックボックスをやめてタグに一本化した。実運用で
# チェックボックスはほぼ使われず（15日間で1回）、同じ日の「散歩」が
# 自由記述タグの側に書かれていたため、手が伸びるほうへ入力口を寄せる。
# タグ名 → daily_logs の列名。警告とカレンダーの⚡は従来どおりこの列を見る。
FLAG_TAGS: dict[str, str] = {
    "没頭": "hyperfocus",
    "散歩": "walk",
    "苦しい夢": "bad_dream",
    "クラッシュ": "crash",
}

FLAG_TAG_ICONS: dict[str, str] = {
    "没頭": "🌀", "散歩": "🚶", "苦しい夢": "😣", "クラッシュ": "⚡",
}

# 言い換えを正規のタグ名へ寄せる。表記ゆれで別タグに散らばると
# フラグが立たず、警告が動かなくなるため。
TAG_ALIASES: dict[str, str] = {
    "没頭タスク": "没頭", "没頭作業": "没頭",
    "散歩15〜20分": "散歩", "さんぽ": "散歩", "ウォーキング": "散歩",
    "悪夢": "苦しい夢", "怖い夢": "苦しい夢", "嫌な夢": "苦しい夢",
    "決壊": "クラッシュ", "クラッシュした": "クラッシュ",
}


def normalize_tag(tag: str) -> str:
    """表記ゆれを正規のタグ名に直す。"""
    tag = tag.strip()
    return TAG_ALIASES.get(tag, tag)


def flags_from_tags(tags: list[str]) -> dict[str, int]:
    """タグ一覧から、フラグ列（hyperfocus/walk/bad_dream/crash）の値を作る。"""
    return {col: int(tag in tags) for tag, col in FLAG_TAGS.items()}


def tags_from_flags(row: sqlite3.Row | None) -> list[str]:
    """既存レコードの立っているフラグを、タグ名として取り出す。

    チェックボックス時代に保存した日を編集しても、フラグが落ちないようにする。
    """
    if row is None:
        return []
    return [tag for tag, col in FLAG_TAGS.items() if row[col]]


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """テーブルがなければ作る。起動時に呼ぶ。"""
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_logs (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                date               TEXT    NOT NULL UNIQUE,  -- YYYY-MM-DD（1日1レコード）
                mp_level           TEXT,                     -- MP_LEVELS のキー
                wake_quality       TEXT,                     -- WAKE_QUALITIES のキー
                bad_dream          INTEGER DEFAULT 0,        -- 苦しい夢を見たか（0/1）
                hyperfocus         INTEGER DEFAULT 0,        -- 没頭タスクをやったか（0/1）
                hyperfocus_minutes INTEGER,                  -- 任意。わかれば分数
                weight_kg          REAL,                     -- 任意
                walk               INTEGER DEFAULT 0,        -- 屋外散歩15〜20分をしたか（0/1）
                activity_tags      TEXT,                     -- カンマ区切り
                crash              INTEGER DEFAULT 0,        -- クラッシュがあったか（0/1）
                note               TEXT,                     -- 一言メモ
                created_at         TEXT    NOT NULL          -- ISO8601
            )
            """
        )
        # 活動を1件1行で持つ。daily_logs（1日1行）は「その日の結果」しか持たず、
        # 何にどれだけ削られたかが後から辿れないため分けた。
        # daily_logs.activity_tags は残す（Webの入力導線はそのまま）。こちらは
        # セッション終了時に Claude が起票する経路で埋まる。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activities (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT    NOT NULL,  -- YYYY-MM-DD（1日に複数行）
                activity     TEXT    NOT NULL,  -- やったこと（1行）
                mp_cost      INTEGER NOT NULL,  -- 実測の消費MP（MP_COSTS のキー）
                mp_estimated INTEGER,           -- /today が提案時に見積もった値。無ければ NULL
                minutes      INTEGER,           -- 任意。わかれば所要分数
                tag          TEXT,              -- 任意。daily_logs.activity_tags と揃えた分類
                source       TEXT,              -- 'session-close' / 'manual' など記録経路
                note         TEXT,
                created_at   TEXT    NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(date)"
        )


def upsert_log(
    log_date: str,
    mp_level: str | None,
    wake_quality: str | None,
    bad_dream: int,
    hyperfocus: int,
    hyperfocus_minutes: int | None,
    weight_kg: float | None,
    walk: int,
    activity_tags: str | None,
    crash: int,
    note: str | None,
) -> None:
    """1日分を保存する。同じ日は全項目を上書き（created_at は初回のまま残す）。"""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO daily_logs (
                date, mp_level, wake_quality, bad_dream, hyperfocus,
                hyperfocus_minutes, weight_kg, walk, activity_tags, crash, note,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                mp_level = excluded.mp_level,
                wake_quality = excluded.wake_quality,
                bad_dream = excluded.bad_dream,
                hyperfocus = excluded.hyperfocus,
                hyperfocus_minutes = excluded.hyperfocus_minutes,
                weight_kg = excluded.weight_kg,
                walk = excluded.walk,
                activity_tags = excluded.activity_tags,
                crash = excluded.crash,
                note = excluded.note
            """,
            (log_date, mp_level, wake_quality, bad_dream, hyperfocus,
             hyperfocus_minutes, weight_kg, walk, activity_tags, crash, note,
             datetime.now().isoformat(timespec="seconds")),
        )


def get_log(log_date: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM daily_logs WHERE date = ?", (log_date,)
        ).fetchone()


def list_recent_logs(days: int = 14) -> list[sqlite3.Row]:
    """直近 `days` 日のうち記録がある日を新しい順に返す。"""
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM daily_logs WHERE date >= ? ORDER BY date DESC",
            (since,),
        ).fetchall()


def list_all_logs() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM daily_logs ORDER BY date DESC"
        ).fetchall()


def add_activity(
    log_date: str,
    activity: str,
    mp_cost: int,
    mp_estimated: int | None = None,
    minutes: int | None = None,
    tag: str | None = None,
    source: str = "session-close",
    note: str | None = None,
) -> int:
    """活動を1件記録する。同じ日に同じ活動名があれば上書きする。

    上書きにしているのは、1日に何度も記録したときや、後から MP を直した
    ときに同じ行が増えないようにするため。別物として2件残したいときは
    activity 名を変える（例:「資料作成 下書き」「資料作成 仕上げ」）。
    """
    if not MP_COST_MIN <= mp_cost <= MP_COST_MAX:
        raise ValueError(
            f"mp_cost は {MP_COST_MIN}〜{MP_COST_MAX} の整数: {mp_cost}")
    if mp_estimated is not None and mp_estimated not in MP_COSTS:
        raise ValueError(
            f"mp_estimated は {sorted(MP_COSTS)} のいずれか: {mp_estimated}")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM activities WHERE date = ? AND activity = ?",
            (log_date, activity),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE activities SET mp_cost = ?, mp_estimated = ?, minutes = ?, "
                "tag = ?, source = ?, note = ? WHERE id = ?",
                (mp_cost, mp_estimated, minutes, tag, source, note, row["id"]),
            )
            return row["id"]
        cur = conn.execute(
            "INSERT INTO activities (date, activity, mp_cost, mp_estimated, "
            "minutes, tag, source, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (log_date, activity, mp_cost, mp_estimated, minutes, tag, source, note,
             datetime.now().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def activities_from_tags(log_date: str) -> list[dict]:
    """その日のタグのうち、活動として未記録のものを TAG_MP_COSTS から起こす。

    外出先での作業のように、**別のPCで行われて自動記録を通らない活動**を
    拾うための経路。Webフォームのタグは後から入力されているので「やった事実」は
    既にDBにあり、欠けているのは消費量だけ、という前提に立っている。

    ⚠ ここで得られる値は表から引いた固定値で、実測ではない。0 の代わりに置く
    「床」として扱うこと。記録が無い日を「使わなかった日」と誤読すると、
    /today が翌日に重い候補を出してしまうのを防ぐのが目的。
    """
    log = get_log(log_date)
    if log is None or not log["activity_tags"]:
        return []

    existing = list_activities(log_date)
    covered = {r["tag"] for r in existing} | {r["activity"] for r in existing}

    out, seen = [], set()
    for raw in log["activity_tags"].split(","):
        tag = normalize_tag(raw)
        if not tag or tag in seen or tag in covered or tag not in TAG_MP_COSTS:
            continue
        seen.add(tag)
        out.append({"activity": tag, "mp_cost": TAG_MP_COSTS[tag], "tag": tag})
    return out


def fill_from_tags(log_date: str) -> list[dict]:
    """`activities_from_tags` の提案をそのまま書き込む。書いた行を返す。

    source は 'table'。実測（'session-close'）と区別できるようにしておくと、
    後から「表の値が実態と合っていたか」を検証できる。
    mp_estimated は入れない（/today の見積もりの答え合わせを固定値で汚さない）。
    """
    proposed = activities_from_tags(log_date)
    for row in proposed:
        add_activity(log_date, row["activity"], row["mp_cost"],
                     tag=row["tag"], source="table")
    return proposed


def list_activities(log_date: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM activities WHERE date = ? ORDER BY id", (log_date,)
        ).fetchall()


def delete_activity(activity_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM activities WHERE id = ?", (activity_id,))


def frequent_tags(days: int = 90, min_count: int = 2, limit: int = 12) -> list[str]:
    """直近 `days` 日でよく使ったタグを多い順に返す。

    自由記述したタグを次回からワンタップで押せるようにするため。
    config.py を編集しなくても語彙が育つ。状態タグ（FLAG_TAGS）は
    別枠で常時表示するのでここには混ぜない。
    """
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT activity_tags FROM daily_logs "
            "WHERE date >= ? AND activity_tags IS NOT NULL",
            (since,),
        ).fetchall()

    counts: dict[str, int] = {}
    for row in rows:
        for tag in row["activity_tags"].split(","):
            tag = normalize_tag(tag)
            if tag and tag not in FLAG_TAGS:
                counts[tag] = counts.get(tag, 0) + 1

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [tag for tag, n in ranked if n >= min_count][:limit]


def hyperfocus_streak(as_of: str) -> int:
    """`as_of`（YYYY-MM-DD）時点の没頭タスク連続日数を返す。

    前日から遡って hyperfocus=1 が続く日数＋当日分（当日が1のときのみ）。
    当日が0や未記録でも過去の連続は途切れ扱いにしない。警告の趣旨が
    「連続したから今日を休息日に」なので、休息日当日にも表示するため。
    記録がない日は「不明」だが、連続の判定上は途切れ扱いにする。
    """
    today = date.fromisoformat(as_of)
    since = (today - timedelta(days=30)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, hyperfocus FROM daily_logs WHERE date BETWEEN ? AND ?",
            (since, as_of),
        ).fetchall()
    focus = {row["date"]: bool(row["hyperfocus"]) for row in rows}

    streak = 1 if focus.get(as_of) else 0
    d = today - timedelta(days=1)
    while focus.get(d.isoformat()):
        streak += 1
        d -= timedelta(days=1)
    return streak


def calendar_months(months: int = 4) -> list[dict]:
    """直近 `months` ヶ月分の月別カレンダー（月曜始まり）を古い順に返す。

    セルは None（月外の詰め物）または dict（day/date/level/crash/future）。
    色分けは mp_level そのもので行い、代表値への変換はテンプレート側で不要にする。
    """
    today = date.today()
    y, m = today.year, today.month
    for _ in range(months - 1):
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    start = date(y, m, 1)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_logs WHERE date >= ?", (start.isoformat(),)
        ).fetchall()
    logs = {row["date"]: row for row in rows}

    result = []
    for _ in range(months):
        first = date(y, m, 1)
        next_first = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
        cells: list[dict | None] = [None] * first.weekday()
        for day_num in range(1, (next_first - first).days + 1):
            d = date(y, m, day_num)
            row = logs.get(d.isoformat())
            cells.append(
                {
                    "day": day_num,
                    "date": d.isoformat(),
                    "level": row["mp_level"] if row else None,
                    "crash": bool(row["crash"]) if row else False,
                    "has_log": row is not None,
                    "future": d > today,
                }
            )
        while len(cells) % 7:
            cells.append(None)
        result.append(
            {
                "label": f"{y}年{m}月",
                "weeks": [cells[i:i + 7] for i in range(0, len(cells), 7)],
            }
        )
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return result


# ---------------------------------------------------------------- 集計・相関

def mp_value(level: str | None) -> int | None:
    """mp_level を集計用の代表値に変換する（未入力は None）。"""
    info = MP_LEVELS.get(level or "")
    return info["value"] if info else None


def mp_band(value: float | None) -> str | None:
    """代表値の平均を、色分け用に最も近い mp_level のキーへ戻す。"""
    if value is None:
        return None
    return min(MP_LEVELS, key=lambda k: abs(MP_LEVELS[k]["value"] - value))


def _avg(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def weekly_summary(weeks: int = 8) -> list[dict]:
    """直近 `weeks` 週（月曜始まり）の集計を新しい順に返す。

    平均MPは代表値の平均。前週差も入れる（週次サマリの目的が水準ではなく
    トレンドの把握なので、単独の平均値より差分のほうが読み取りやすい）。
    ただし記録が `MIN_WEEK_DAYS` 日に満たない週は差の計算から除く。
    1日だけ記録した週の平均を週の代表値として比べると差が誇張されるため。
    """
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    start = this_monday - timedelta(weeks=weeks - 1)

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_logs WHERE date >= ? ORDER BY date",
            (start.isoformat(),),
        ).fetchall()

    buckets: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        d = date.fromisoformat(row["date"])
        monday = (d - timedelta(days=d.weekday())).isoformat()
        buckets.setdefault(monday, []).append(row)

    result = []
    prev_avg: float | None = None
    for i in range(weeks):  # 古い順に組み立てて前週差を出し、最後に反転する
        monday = start + timedelta(weeks=i)
        sunday = monday + timedelta(days=6)
        logs = buckets.get(monday.isoformat(), [])
        avg = _avg([v for v in (mp_value(r["mp_level"]) for r in logs) if v is not None])
        solid = len(logs) >= MIN_WEEK_DAYS  # 差の計算に使えるだけ記録がある週か
        result.append(
            {
                "start": monday.isoformat(),
                "label": f"{monday.month}/{monday.day}〜{sunday.month}/{sunday.day}",
                "current": monday <= today <= sunday,
                "logged": len(logs),
                "sparse": not solid,
                "avg_mp": avg,
                "band": mp_band(avg),
                "delta": None if not (solid and avg is not None and prev_avg is not None)
                         else round(avg - prev_avg, 1),
                "crash": sum(1 for r in logs if r["crash"]),
                "bad_wake": sum(1 for r in logs if r["wake_quality"] == "bad"),
                "bad_dream": sum(1 for r in logs if r["bad_dream"]),
                "hyperfocus": sum(1 for r in logs if r["hyperfocus"]),
                "walk": sum(1 for r in logs if r["walk"]),
            }
        )
        # 記録の薄い週を挟んだら比較を打ち切る。何週も離れた週との差を
        # 「前週差」として出すと、間の空白がなかったように見えてしまうため。
        prev_avg = avg if (solid and avg is not None) else None
    result.reverse()
    return result


def wake_vs_mp() -> dict[str, list[dict]]:
    """寝起きの良し悪しと、同日／翌日のMPの関係を返す。

    mood-health.md「寝起きの悪さ→当日の決壊しやすさ」仮説の検証用。
    翌日分は暦の翌日に記録がある場合だけ数える（記録の穴を跨いで
    「翌日」と見なすと因果が薄まるため）。
    """
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM daily_logs ORDER BY date").fetchall()
    logs = {row["date"]: row for row in rows}

    same: dict[str, list[int]] = {}
    nxt: dict[str, list[int]] = {}
    for row in rows:
        wake = row["wake_quality"]
        if not wake:
            continue
        v = mp_value(row["mp_level"])
        if v is not None:
            same.setdefault(wake, []).append(v)
        tomorrow = (date.fromisoformat(row["date"]) + timedelta(days=1)).isoformat()
        if tomorrow in logs:
            v2 = mp_value(logs[tomorrow]["mp_level"])
            if v2 is not None:
                nxt.setdefault(wake, []).append(v2)

    def rows_for(source: dict[str, list[int]]) -> list[dict]:
        out = []
        for key, label in WAKE_QUALITIES.items():
            values = source.get(key, [])
            avg = _avg(values)
            out.append({"key": key, "label": label, "n": len(values), "avg": avg,
                        "band": mp_band(avg), "enough": len(values) >= MIN_SAMPLES})
        return out

    return {"same_day": rows_for(same), "next_day": rows_for(nxt)}


def tag_vs_mp(limit: int = 10) -> list[dict]:
    """活動タグ別の同日MP平均を、出現回数の多い順に返す。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT activity_tags, mp_level FROM daily_logs "
            "WHERE activity_tags IS NOT NULL AND mp_level IS NOT NULL"
        ).fetchall()

    buckets: dict[str, list[int]] = {}
    for row in rows:
        value = mp_value(row["mp_level"])
        for tag in row["activity_tags"].split(","):
            tag = tag.strip()
            if tag and value is not None:
                buckets.setdefault(tag, []).append(value)

    out = []
    for tag, values in buckets.items():
        avg = _avg(values)
        out.append({"tag": tag, "n": len(values), "avg": avg,
                    "band": mp_band(avg), "enough": len(values) >= MIN_SAMPLES})
    out.sort(key=lambda x: (-x["n"], x["tag"]))
    return out[:limit]


def weight_series() -> list[dict]:
    """体重を記録した日を古い順に返す（未記録なら空リスト）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date, weight_kg, mp_level FROM daily_logs "
            "WHERE weight_kg IS NOT NULL ORDER BY date"
        ).fetchall()
    return [{"date": r["date"], "weight": r["weight_kg"],
             "mp": mp_value(r["mp_level"])} for r in rows]


def daily_mp_spent(days: int = 14) -> list[dict]:
    """直近 `days` 日の消費MP合計を、新しい順に返す。

    /today が「昨日どれだけ使ったか」を見て今日の重さを決めるための入力。
    活動を1件も記録していない日は行ごと出てこない（0と未記録は別物なので、
    ここで 0 埋めしない）。
    """
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.date, SUM(a.mp_cost) spent, COUNT(*) n, "
            "       SUM(a.minutes) minutes, d.mp_level "
            "FROM activities a LEFT JOIN daily_logs d ON d.date = a.date "
            "WHERE a.date >= ? GROUP BY a.date ORDER BY a.date DESC",
            (since,),
        ).fetchall()
    return [{"date": r["date"], "spent": r["spent"], "n": r["n"],
             "minutes": r["minutes"], "mp_level": r["mp_level"],
             "mp_value": mp_value(r["mp_level"])} for r in rows]


def estimate_accuracy(days: int = 90) -> dict:
    """/today の見積もり（mp_estimated）と実測（mp_cost）のズレを返す。

    見積もりが入っている行だけを対象にする。件数が MIN_SAMPLES 未満のうちは
    `enough` が False になるので、参考値として扱うこと（数件の平均で
    見積もりルールを書き換えると、かえって外れるようになる）。
    """
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT mp_estimated, mp_cost FROM activities "
            "WHERE date >= ? AND mp_estimated IS NOT NULL",
            (since,),
        ).fetchall()

    buckets: dict[int, list[int]] = {}
    for row in rows:
        buckets.setdefault(row["mp_estimated"], []).append(row["mp_cost"])

    out = []
    for est in sorted(MP_COSTS):
        actuals = buckets.get(est, [])
        avg = _avg(actuals)
        out.append({"estimated": est, "label": MP_COSTS[est], "n": len(actuals),
                    "actual_avg": avg,
                    "bias": None if avg is None else round(avg - est, 2),
                    "enough": len(actuals) >= MIN_SAMPLES})
    total = len(rows)
    hits = sum(1 for r in rows if r["mp_estimated"] == r["mp_cost"])
    return {"by_estimate": out, "n": total,
            "hit_rate": None if total == 0 else round(hits / total, 2),
            "enough": total >= MIN_SAMPLES}


def overall_stats() -> dict:
    """ふりかえりページの前置きに出す全体の記録状況。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) n, MIN(date) first, MAX(date) last, "
            "COUNT(mp_level) mp, COUNT(weight_kg) weight FROM daily_logs"
        ).fetchone()
    return {"days": row["n"], "first": row["first"], "last": row["last"],
            "mp_days": row["mp"], "weight_days": row["weight"]}
