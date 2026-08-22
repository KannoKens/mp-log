# -*- coding: utf-8 -*-
"""Web 層（main.py）のテスト。

HTML の見た目は追わない。追うのは **フォームから来た値がどう解釈されるか**
（不正な値を捨てる・タグからフラグを立てる・送られてこなかった項目を消さない）と、
警告が出る条件。ここが崩れると、記録が静かに壊れる。
"""

import pytest
from fastapi.testclient import TestClient

import db
import main


@pytest.fixture
def client(isolated_db):
    with TestClient(main.app) as c:
        yield c


def _post(client, **fields):
    """POST /logs をリダイレクトを追わずに叩く。"""
    return client.post("/logs", data=fields, follow_redirects=False)


# ------------------------------------------------------------------ 画面の表示

def test_index_renders_today(client, today):
    res = client.get("/")
    assert res.status_code == 200
    assert today.isoformat() in res.text


def test_index_accepts_past_date(client, add_log, days_ago):
    add_log(days_ago(3), "caution", "bad", note="3日前のメモ")
    res = client.get("/", params={"d": days_ago(3)})
    assert res.status_code == 200
    assert "3日前のメモ" in res.text


def test_index_falls_back_to_today_on_broken_date(client, today):
    """?d= が壊れていても 500 にせず今日にフォールバックすること。"""
    res = client.get("/", params={"d": "2026-13-99"})
    assert res.status_code == 200
    assert today.isoformat() in res.text


def test_index_warns_on_hyperfocus_streak(client, add_log, days_ago):
    for n in (0, 1, 2):
        add_log(days_ago(n), "safe", hyperfocus=1)
    res = client.get("/")
    assert "3日連続" in res.text
    assert "休息日" in res.text


def test_index_warns_on_bad_wake(client, add_log, days_ago):
    add_log(days_ago(0), "caution", "bad")
    assert "減速日" in client.get("/").text


def test_index_has_no_warning_on_a_calm_day(client, add_log, days_ago):
    add_log(days_ago(0), "safe", "good")
    text = client.get("/").text
    assert "減速日" not in text and "休息日" not in text


def test_calendar_and_insights_render(client, add_log, days_ago):
    add_log(days_ago(0), "safe", "good", activity_tags="家事", weight_kg=60.0)
    assert client.get("/calendar").status_code == 200
    assert client.get("/insights").status_code == 200


def test_pages_render_on_a_brand_new_database(client):
    """初回起動（記録ゼロ）でも全ページが描けること。

    集計は平均や前週差を扱うので、空のときに落ちやすい。ここで落ちると
    「入れた直後に壊れている」ことになり、使い始める前に離脱する。
    """
    for path in ("/", "/calendar", "/insights", "/api/logs"):
        assert client.get(path).status_code == 200, path


def test_calendar_and_insights_reject_out_of_range_query(client):
    """months/weeks の範囲外は 422。巨大な値でクエリを走らせない。"""
    assert client.get("/calendar", params={"months": 0}).status_code == 422
    assert client.get("/calendar", params={"months": 99}).status_code == 422
    assert client.get("/insights", params={"weeks": 0}).status_code == 422


# ------------------------------------------------------------------ 保存

def test_save_log_redirects_to_that_day(client, days_ago):
    res = _post(client, log_date=days_ago(1), mp_level="safe")
    assert res.status_code == 303
    assert res.headers["location"] == f"/?d={days_ago(1)}"


def test_save_log_persists_fields(client, days_ago):
    day = days_ago(0)
    _post(client, log_date=day, mp_level="caution", wake_quality="bad", note="  メモ  ")

    row = db.get_log(day)
    assert (row["mp_level"], row["wake_quality"]) == ("caution", "bad")
    assert row["note"] == "メモ", "前後の空白は落とす"


def test_save_log_raises_flags_from_tags(client, days_ago):
    day = days_ago(0)
    _post(client, log_date=day, mp_level="danger", tags=["家事", "散歩", "クラッシュ"])

    row = db.get_log(day)
    assert (row["walk"], row["crash"]) == (1, 1)
    assert (row["hyperfocus"], row["bad_dream"]) == (0, 0)
    assert row["activity_tags"] == "家事,散歩,クラッシュ"


def test_save_log_normalizes_and_dedupes_free_tags(client, days_ago):
    """読点区切りを許容し、ボタンと自由記入の重複を畳むこと。"""
    day = days_ago(0)
    _post(client, log_date=day, mp_level="safe", tags=["散歩"],
          tags_free="さんぽ、買い物, 通院 ")

    assert db.get_log(day)["activity_tags"] == "散歩,買い物,通院"


def test_save_log_drops_unknown_vocabulary(client, days_ago):
    """語彙外の mp_level / wake_quality は保存しない（集計の代表値が引けないため）。"""
    day = days_ago(0)
    _post(client, log_date=day, mp_level="最強", wake_quality="ふつう")

    row = db.get_log(day)
    assert row["mp_level"] is None and row["wake_quality"] is None


def test_save_log_stores_empty_tags_as_null(client, days_ago):
    day = days_ago(0)
    _post(client, log_date=day, mp_level="safe", tags_free="  , 、 ")
    assert db.get_log(day)["activity_tags"] is None


def test_save_log_keeps_fields_absent_from_the_form(client, add_log, days_ago):
    """体重・没頭分数はフォームに無い。再保存で消えないこと。"""
    day = days_ago(0)
    add_log(day, "safe", weight_kg=61.2, hyperfocus_minutes=90)

    _post(client, log_date=day, mp_level="danger")

    row = db.get_log(day)
    assert row["mp_level"] == "danger"
    assert (row["weight_kg"], row["hyperfocus_minutes"]) == (61.2, 90)


def test_save_log_falls_back_to_today_on_broken_date(client, today):
    res = _post(client, log_date="not-a-date", mp_level="safe")
    assert res.headers["location"] == f"/?d={today.isoformat()}"
    assert db.get_log(today.isoformat()) is not None


# ------------------------------------------------------------------ API

def test_api_logs_returns_all_rows_newest_first(client, add_log, days_ago):
    add_log(days_ago(1), "safe", "good")
    add_log(days_ago(0), "danger", "bad")

    rows = client.get("/api/logs").json()

    assert [r["date"] for r in rows] == [days_ago(0), days_ago(1)]
    assert rows[0]["mp_level"] == "danger"


def test_api_logs_is_empty_without_records(client):
    assert client.get("/api/logs").json() == []
