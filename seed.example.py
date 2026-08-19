# -*- coding: utf-8 -*-
"""過去記録シードのサンプル。`cp seed.example.py seed.py` で複製し、
自分の記録に書き換えて `python3 seed.py` で投入する。seed.py は .gitignore 済み。

既にレコードがある日はスキップする（手入力を上書きしないため）。
"""

import db

# フォーマットのデモ用の匿名サンプル（全項目とも任意入力）
SEED_LOGS: list[dict] = [
    {"log_date": "2026-01-06", "mp_level": "safe", "wake_quality": "good",
     "activity_tags": "仕事", "note": "サンプル記録"},
    {"log_date": "2026-01-07", "hyperfocus": 1, "hyperfocus_minutes": 180,
     "activity_tags": "仕事,勉強"},
    {"log_date": "2026-01-08", "hyperfocus": 1,
     "note": "没頭タスク2日連続（連続警告のデモ）"},
    {"log_date": "2026-01-09", "mp_level": "caution", "wake_quality": "bad",
     "bad_dream": 1, "note": "寝起きが悪い→減速日のデモ"},
    {"log_date": "2026-01-10", "mp_level": "danger", "crash": 1,
     "note": "クラッシュのデモ（カレンダーで⚡マーカー）"},
]

DEFAULTS: dict = {
    "mp_level": None, "wake_quality": None, "bad_dream": 0, "hyperfocus": 0,
    "hyperfocus_minutes": None, "weight_kg": None, "walk": 0,
    "activity_tags": None, "crash": 0, "note": None,
}


def main() -> None:
    db.init_db()
    for log in SEED_LOGS:
        if db.get_log(log["log_date"]) is not None:
            print(f"skip: {log['log_date']}（既に記録あり）")
            continue
        db.upsert_log(**{**DEFAULTS, **log})
        print(f"seed: {log['log_date']}")


if __name__ == "__main__":
    main()
