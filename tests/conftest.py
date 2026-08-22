# -*- coding: utf-8 -*-
"""テスト共通の下ごしらえ。

一番大事なのは `_isolated_db` が autouse であること。このアプリの DB は
リポジトリ直下の mplog.db 固定で、テストが1つでも素の db.get_conn() を呼ぶと
**実際の記録を書き換えてしまう**。fixture を明示的に要求し忘れても壊れないよう、
オプトアウトではなく autouse にしてある。
"""

from datetime import date, timedelta

import pytest

import db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """DB をテストごとの空ファイルに差し替える。

    db.DB_PATH は get_conn() の中で毎回参照されるので、モジュール属性を
    差し替えるだけで main.py / remind.py 側（どちらも db モジュールを
    import している）にもそのまま効く。
    """
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "mplog.db")
    db.init_db()
    return db


@pytest.fixture
def today():
    return date.today()


@pytest.fixture
def days_ago(today):
    """`days_ago(2)` で2日前の YYYY-MM-DD を返す。"""
    def _days_ago(n: int) -> str:
        return (today - timedelta(days=n)).isoformat()
    return _days_ago


@pytest.fixture
def add_log():
    """日次ログを1件入れる。指定しなかった項目は空で埋める。

    upsert_log は引数が11個の位置引数で、テストごとに全部並べると
    「その test が何を主張しているのか」が埋もれるのでラップする。
    """
    def _add_log(
        log_date: str,
        mp_level: str | None = None,
        wake_quality: str | None = None,
        *,
        bad_dream: int = 0,
        hyperfocus: int = 0,
        hyperfocus_minutes: int | None = None,
        weight_kg: float | None = None,
        walk: int = 0,
        activity_tags: str | None = None,
        crash: int = 0,
        note: str | None = None,
    ) -> None:
        db.upsert_log(
            log_date, mp_level, wake_quality, bad_dream, hyperfocus,
            hyperfocus_minutes, weight_kg, walk, activity_tags, crash, note,
        )
    return _add_log
