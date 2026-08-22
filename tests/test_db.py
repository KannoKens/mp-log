# -*- coding: utf-8 -*-
"""永続化層（db.py）のテスト。

方針: 「SQL が動くこと」ではなく、**docstring に書いてある判断のルール**を
固定する。0 と未記録を区別する・記録の薄い週を比較から外す・件数が少ない
うちは enough=False にする、といったルールはコードを読んでも意図が
分からなくなりやすく、うっかり単純化されると体調の判断材料が壊れる。
"""

from datetime import date, timedelta

import pytest

import db


# ------------------------------------------------------------------ タグの正規化

def test_normalize_tag_maps_aliases_and_strips_space():
    assert db.normalize_tag(" さんぽ ") == "散歩"
    assert db.normalize_tag("悪夢") == "苦しい夢"
    assert db.normalize_tag("没頭タスク") == "没頭"


def test_normalize_tag_passes_unknown_tag_through():
    assert db.normalize_tag("家事") == "家事"


def test_flags_from_tags_sets_only_listed_flags():
    flags = db.flags_from_tags(["散歩", "家事"])
    assert flags == {"hyperfocus": 0, "walk": 1, "bad_dream": 0, "crash": 0}


def test_tags_from_flags_restores_checkbox_era_records(add_log, days_ago):
    """チェックボックス時代に立てたフラグが、編集時にタグとして戻ってくること。"""
    day = days_ago(0)
    add_log(day, "safe", hyperfocus=1, walk=1)
    tags = db.tags_from_flags(db.get_log(day))
    assert set(tags) == {"没頭", "散歩"}


def test_tags_from_flags_accepts_missing_row():
    assert db.tags_from_flags(None) == []


# ------------------------------------------------------------------ daily_logs

def test_get_log_returns_none_for_unrecorded_day(days_ago):
    assert db.get_log(days_ago(3)) is None


def test_upsert_log_overwrites_same_day_but_keeps_created_at(add_log, days_ago):
    day = days_ago(0)
    add_log(day, "danger", "bad", note="最初")
    created = db.get_log(day)["created_at"]

    add_log(day, "safe", "good", note="上書き")
    row = db.get_log(day)

    assert (row["mp_level"], row["wake_quality"], row["note"]) == ("safe", "good", "上書き")
    assert row["created_at"] == created, "created_at は初回の値を残す"


def test_list_recent_logs_excludes_days_outside_window(add_log, days_ago):
    add_log(days_ago(0), "safe")
    add_log(days_ago(20), "safe")
    dates = [r["date"] for r in db.list_recent_logs(days=14)]
    assert dates == [days_ago(0)]


def test_list_all_logs_is_newest_first(add_log, days_ago):
    add_log(days_ago(2), "safe")
    add_log(days_ago(0), "safe")
    assert [r["date"] for r in db.list_all_logs()] == [days_ago(0), days_ago(2)]


# ------------------------------------------------------------------ activities

def test_add_activity_rejects_out_of_range_cost(days_ago):
    with pytest.raises(ValueError):
        db.add_activity(days_ago(0), "重すぎる作業", db.MP_COST_MAX + 1)
    with pytest.raises(ValueError):
        db.add_activity(days_ago(0), "軽すぎる作業", 0)


def test_add_activity_rejects_estimate_outside_vocabulary(days_ago):
    """見積もりの語彙(1/3/5)を勝手に増やせないこと。増えると較正が壊れる。"""
    with pytest.raises(ValueError):
        db.add_activity(days_ago(0), "作業", 2, mp_estimated=2)


def test_add_activity_updates_instead_of_duplicating(days_ago):
    day = days_ago(0)
    first = db.add_activity(day, "資料作成", 3, minutes=60)
    second = db.add_activity(day, "資料作成", 5, minutes=120)

    rows = db.list_activities(day)
    assert first == second
    assert len(rows) == 1, "同じ日の同じ活動名は1行に畳む"
    assert (rows[0]["mp_cost"], rows[0]["minutes"]) == (5, 120)


def test_delete_activity_removes_only_that_row(days_ago):
    day = days_ago(0)
    keep = db.add_activity(day, "残す", 1)
    drop = db.add_activity(day, "消す", 1)
    db.delete_activity(drop)
    assert [r["id"] for r in db.list_activities(day)] == [keep]


# ------------------------------------------------------------- タグ→活動の補完

def test_activities_from_tags_proposes_only_uncovered_tags(add_log, days_ago, monkeypatch):
    monkeypatch.setattr(db, "TAG_MP_COSTS", {"家事": 2, "買い物": 1, "運転": 3})
    day = days_ago(1)
    add_log(day, "safe", activity_tags="家事,買い物,未登録のタグ")
    db.add_activity(day, "買い物", 1, tag="買い物")  # すでに記録済み

    proposed = db.activities_from_tags(day)

    assert proposed == [{"activity": "家事", "mp_cost": 2, "tag": "家事"}], (
        "記録済みのタグと、表に載っていないタグは提案しない"
    )


def test_activities_from_tags_normalizes_and_dedupes(add_log, days_ago, monkeypatch):
    monkeypatch.setattr(db, "TAG_MP_COSTS", {"散歩": 1})
    day = days_ago(1)
    add_log(day, "safe", activity_tags="さんぽ,散歩,ウォーキング")
    assert db.activities_from_tags(day) == [
        {"activity": "散歩", "mp_cost": 1, "tag": "散歩"}
    ]


def test_activities_from_tags_returns_empty_without_log(days_ago, monkeypatch):
    monkeypatch.setattr(db, "TAG_MP_COSTS", {"家事": 2})
    assert db.activities_from_tags(days_ago(5)) == []


def test_fill_from_tags_marks_source_table_and_leaves_estimate_empty(
    add_log, days_ago, monkeypatch
):
    """表から引いた固定値が、見積もりの答え合わせに混ざらないこと。"""
    monkeypatch.setattr(db, "TAG_MP_COSTS", {"家事": 2})
    day = days_ago(1)
    add_log(day, "safe", activity_tags="家事")

    db.fill_from_tags(day)
    row = db.list_activities(day)[0]

    assert (row["activity"], row["mp_cost"], row["source"]) == ("家事", 2, "table")
    assert row["mp_estimated"] is None


def test_fill_from_tags_is_idempotent(add_log, days_ago, monkeypatch):
    monkeypatch.setattr(db, "TAG_MP_COSTS", {"家事": 2})
    day = days_ago(1)
    add_log(day, "safe", activity_tags="家事")
    db.fill_from_tags(day)
    assert db.fill_from_tags(day) == [], "2回目は提案が空になる"
    assert len(db.list_activities(day)) == 1


# ------------------------------------------------------------------ 没頭の連続

def test_hyperfocus_streak_counts_consecutive_days(add_log, days_ago):
    for n in (0, 1, 2):
        add_log(days_ago(n), "safe", hyperfocus=1)
    assert db.hyperfocus_streak(days_ago(0)) == 3


def test_hyperfocus_streak_survives_a_rest_day_today(add_log, days_ago):
    """休息日当日にも「2日連続した」と表示できること（警告の趣旨）。"""
    add_log(days_ago(2), "safe", hyperfocus=1)
    add_log(days_ago(1), "safe", hyperfocus=1)
    add_log(days_ago(0), "safe", hyperfocus=0)
    assert db.hyperfocus_streak(days_ago(0)) == 2


def test_hyperfocus_streak_breaks_on_unrecorded_day(add_log, days_ago):
    add_log(days_ago(3), "safe", hyperfocus=1)
    # days_ago(2) は未記録
    add_log(days_ago(1), "safe", hyperfocus=1)
    add_log(days_ago(0), "safe", hyperfocus=1)
    assert db.hyperfocus_streak(days_ago(0)) == 2


# ------------------------------------------------------------------ 代表値の変換

def test_mp_value_maps_level_to_representative_number():
    assert db.mp_value("safe") == 10
    assert db.mp_value(None) is None
    assert db.mp_value("存在しない") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [(17, "excellent"), (10, "safe"), (7, "caution"), (3, "danger"), (14, "excellent")],
)
def test_mp_band_snaps_to_nearest_level(value, expected):
    assert db.mp_band(value) == expected


def test_mp_band_passes_through_none():
    assert db.mp_band(None) is None


# ------------------------------------------------------------------ 消費MPの集計

def test_daily_mp_spent_sums_per_day_and_joins_level(add_log, days_ago):
    day = days_ago(1)
    add_log(day, "caution")
    db.add_activity(day, "作業A", 3, minutes=60)
    db.add_activity(day, "作業B", 2, minutes=30)

    row = db.daily_mp_spent(days=7)[0]

    assert row["date"] == day
    assert (row["spent"], row["n"], row["minutes"]) == (5, 2, 90)
    assert (row["mp_level"], row["mp_value"]) == ("caution", 7)


def test_daily_mp_spent_omits_days_without_activities(add_log, days_ago):
    """0 と未記録を区別する（0埋めすると「使わなかった日」と誤読される）。"""
    add_log(days_ago(0), "safe")  # ログはあるが活動は未記録
    db.add_activity(days_ago(1), "作業", 1)
    assert [r["date"] for r in db.daily_mp_spent(days=7)] == [days_ago(1)]


def test_daily_mp_spent_is_newest_first(days_ago):
    db.add_activity(days_ago(2), "古い", 1)
    db.add_activity(days_ago(0), "新しい", 1)
    assert [r["date"] for r in db.daily_mp_spent(days=7)] == [days_ago(0), days_ago(2)]


# ------------------------------------------------------------ 見積もりの答え合わせ

def test_estimate_accuracy_reports_bias_and_hit_rate(days_ago):
    day = days_ago(0)
    for i in range(3):
        db.add_activity(day, f"軽いつもりだった{i}", 2, mp_estimated=1)
    for i in range(2):
        db.add_activity(day, f"当たった{i}", 3, mp_estimated=3)

    result = db.estimate_accuracy()
    by_est = {r["estimated"]: r for r in result["by_estimate"]}

    assert by_est[1]["n"] == 3
    assert by_est[1]["actual_avg"] == 2.0
    assert by_est[1]["bias"] == 1.0, "MP1 と見積もった作業が実際は重かった"
    assert by_est[3]["bias"] == 0.0
    assert by_est[5]["n"] == 0 and by_est[5]["bias"] is None
    assert result["n"] == 5
    assert result["hit_rate"] == 0.4


def test_estimate_accuracy_ignores_activities_without_estimate(days_ago):
    db.add_activity(days_ago(0), "見積もりなし", 3)
    result = db.estimate_accuracy()
    assert result["n"] == 0
    assert result["hit_rate"] is None
    assert result["enough"] is False


def test_estimate_accuracy_needs_five_samples_to_be_trusted(days_ago):
    """4件では enough=False、5件目で True。

    件数は定数から引かずに直書きする。db.MIN_SAMPLES を参照して作ると
    「閾値がいくつでも通るテスト」になり、しきい値の変更を検出できない。
    """
    day = days_ago(0)
    for i in range(4):
        db.add_activity(day, f"作業{i}", 3, mp_estimated=3)
    assert db.estimate_accuracy()["enough"] is False

    db.add_activity(day, "作業4", 3, mp_estimated=3)
    assert db.estimate_accuracy()["enough"] is True


# ------------------------------------------------------------------ よく使うタグ

def test_frequent_tags_needs_min_count_and_skips_state_tags(add_log, days_ago):
    add_log(days_ago(0), "safe", activity_tags="家事,散歩")
    add_log(days_ago(1), "safe", activity_tags="家事,買い物")
    add_log(days_ago(2), "safe", activity_tags="さんぽ,家事")

    tags = db.frequent_tags()

    assert tags == ["家事"], "1回だけの買い物と、状態タグの散歩は出さない"


def test_frequent_tags_orders_by_count_then_name(add_log, days_ago):
    for n in range(4):
        add_log(days_ago(n), "safe", activity_tags="家事,勉強" if n < 3 else "勉強")
    assert db.frequent_tags() == ["勉強", "家事"]


# ------------------------------------------------------------------ 週次サマリ

def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def test_weekly_summary_needs_three_days_to_count_as_a_week(add_log, today):
    """2日だけの週は sparse、3日で solid。境界は直書きで固定する。"""
    monday = _monday_of(today)
    for i in range(2):
        add_log((monday + timedelta(days=i)).isoformat(), "safe")
    assert db.weekly_summary(weeks=1)[0]["sparse"] is True

    add_log((monday + timedelta(days=2)).isoformat(), "safe")
    assert db.weekly_summary(weeks=1)[0]["sparse"] is False


def test_weekly_summary_reports_delta_against_previous_week(add_log, today):
    this_monday = _monday_of(today)
    last_monday = this_monday - timedelta(days=7)
    for i in range(3):
        add_log((last_monday + timedelta(days=i)).isoformat(), "safe")
        add_log((this_monday + timedelta(days=i)).isoformat(), "excellent")

    weeks = {w["start"]: w for w in db.weekly_summary(weeks=4)}

    assert weeks[this_monday.isoformat()]["avg_mp"] == 17.0
    assert weeks[this_monday.isoformat()]["band"] == "excellent"
    assert weeks[this_monday.isoformat()]["delta"] == 7.0
    assert weeks[this_monday.isoformat()]["sparse"] is False


def test_weekly_summary_stops_comparing_across_a_sparse_week(add_log, today):
    """記録の薄い週を挟んだら比較を打ち切る（空白を無かったことにしない）。"""
    this_monday = _monday_of(today)
    sparse_monday = this_monday - timedelta(days=7)
    old_monday = this_monday - timedelta(days=14)

    for i in range(3):
        add_log((old_monday + timedelta(days=i)).isoformat(), "safe")
        add_log((this_monday + timedelta(days=i)).isoformat(), "excellent")
    for i in range(2):  # 記録が足りない週
        add_log((sparse_monday + timedelta(days=i)).isoformat(), "danger")

    weeks = {w["start"]: w for w in db.weekly_summary(weeks=4)}

    assert weeks[sparse_monday.isoformat()]["sparse"] is True
    assert weeks[sparse_monday.isoformat()]["delta"] is None
    assert weeks[this_monday.isoformat()]["delta"] is None


def test_weekly_summary_counts_flags(add_log, today):
    this_monday = _monday_of(today)
    add_log(this_monday.isoformat(), "danger", "bad", crash=1, bad_dream=1,
            hyperfocus=1, walk=1)

    week = db.weekly_summary(weeks=1)[0]

    assert week["current"] is True
    assert week["logged"] == 1
    assert (week["crash"], week["bad_wake"], week["bad_dream"]) == (1, 1, 1)
    assert (week["hyperfocus"], week["walk"]) == (1, 1)


def test_weekly_summary_returns_newest_first(today):
    weeks = db.weekly_summary(weeks=3)
    assert len(weeks) == 3
    assert weeks[0]["start"] > weeks[-1]["start"]
    assert weeks[0]["start"] == _monday_of(today).isoformat()


# ------------------------------------------------------------------ カレンダー

def test_calendar_months_pads_to_full_weeks(add_log, today):
    months = db.calendar_months(months=1)

    assert len(months) == 1
    assert months[0]["label"] == f"{today.year}年{today.month}月"
    assert all(len(week) == 7 for week in months[0]["weeks"])


def test_calendar_months_marks_logged_day(add_log, today):
    add_log(today.isoformat(), "danger", crash=1)

    cells = [c for week in db.calendar_months(months=1)[0]["weeks"] for c in week if c]
    cell = next(c for c in cells if c["date"] == today.isoformat())

    assert cell["level"] == "danger"
    assert cell["crash"] is True
    assert cell["has_log"] is True
    assert cell["future"] is False


def test_calendar_months_marks_unlogged_and_future_days(today):
    cells = [c for week in db.calendar_months(months=1)[0]["weeks"] for c in week if c]
    cell = next(c for c in cells if c["date"] == today.isoformat())

    assert cell["has_log"] is False and cell["level"] is None
    assert all(c["future"] for c in cells if c["date"] > today.isoformat())


# ------------------------------------------------------------------ 相関ビュー

def test_wake_vs_mp_separates_same_day_from_next_day(add_log, days_ago):
    add_log(days_ago(1), "excellent", "good")
    add_log(days_ago(0), "caution", "bad")

    result = db.wake_vs_mp()
    same = {r["key"]: r for r in result["same_day"]}
    nxt = {r["key"]: r for r in result["next_day"]}

    assert same["good"]["avg"] == 17.0 and same["good"]["n"] == 1
    assert same["bad"]["avg"] == 7.0
    assert nxt["good"]["avg"] == 7.0, "寝起きが良かった日の翌日のMP"
    assert nxt["bad"]["n"] == 0, "翌日に記録が無ければ数えない"
    assert same["good"]["enough"] is False


def test_wake_vs_mp_skips_gap_between_records(add_log, days_ago):
    add_log(days_ago(3), "excellent", "good")
    add_log(days_ago(0), "danger", "good")  # 暦の翌日ではない
    nxt = {r["key"]: r for r in db.wake_vs_mp()["next_day"]}
    assert nxt["good"]["n"] == 0


def test_tag_vs_mp_averages_by_tag(add_log, days_ago):
    add_log(days_ago(0), "safe", activity_tags="家事,勉強")
    add_log(days_ago(1), "danger", activity_tags="勉強")

    rows = {r["tag"]: r for r in db.tag_vs_mp()}

    assert rows["勉強"]["n"] == 2 and rows["勉強"]["avg"] == 6.5
    assert rows["家事"]["n"] == 1 and rows["家事"]["avg"] == 10.0
    assert rows["勉強"]["enough"] is False


def test_weight_series_returns_only_recorded_days(add_log, days_ago):
    add_log(days_ago(1), "safe", weight_kg=60.5)
    add_log(days_ago(0), "safe")  # 体重なし
    assert db.weight_series() == [
        {"date": days_ago(1), "weight": 60.5, "mp": 10}
    ]


def test_overall_stats_summarises_coverage(add_log, days_ago):
    add_log(days_ago(2), "safe", weight_kg=60.0)
    add_log(days_ago(1), None)          # MP 未入力の日
    add_log(days_ago(0), "danger")

    stats = db.overall_stats()

    assert stats["days"] == 3
    assert (stats["first"], stats["last"]) == (days_ago(2), days_ago(0))
    assert stats["mp_days"] == 2, "mp_level が入っている日だけ数える"
    assert stats["weight_days"] == 1


def test_overall_stats_on_empty_db():
    stats = db.overall_stats()
    assert stats["days"] == 0
    assert stats["first"] is None and stats["last"] is None
