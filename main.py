# -*- coding: utf-8 -*-
"""MPログ（日次のセルフトラッキング）— FastAPI アプリ本体（MVP）。

今の機能:
  - GET  /          入力フォーム（?d=YYYY-MM-DD で過去日も編集可）＋直近の一覧＋警告表示
  - POST /logs      1日分を保存（UPSERT）→ その日の / にリダイレクト
  - GET  /calendar  カレンダーヒートマップ（mp_level で色分け・crash マーカー）
  - GET  /insights  ふりかえり（週次サマリ＋寝起き・活動タグとMPの関係）
  - GET  /api/logs  全件を JSON で返す

まだ入れていない（次の段階）: Claude API での一言フィードバック。
"""

from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

import db

app = FastAPI(title="MPログ")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


def _valid_date(s: str | None) -> str | None:
    try:
        return date.fromisoformat(s or "").isoformat()
    except ValueError:
        return None


def _warnings(today: str) -> list[dict]:
    """トップページに出す警告（brain-fatigue.md の仮ルールのコード化）。"""
    warns = []
    streak = db.hyperfocus_streak(today)
    if streak >= 3:
        warns.append({"level": "strong",
                      "text": f"没頭タスクが{streak}日連続しています。上限（2〜3日）を超えました。"
                              "今日は休息日にしてください。"})
    elif streak == 2:
        warns.append({"level": "mild",
                      "text": "没頭タスクが2日連続しています。そろそろ休息日の挿入を"
                              "（2〜3日で1日休息のルール）。"})

    log = db.get_log(today)
    if log and (log["wake_quality"] == "bad" or log["bad_dream"]):
        warns.append({"level": "mild",
                      "text": "今日は減速日。寝起きが悪い朝は決壊しやすい日です。"
                              "没頭タスク・大きな決断は避けて、小タスクと回復に充てましょう。"})
    return warns


@app.get("/")
def index(request: Request, d: str | None = None):
    today = date.today().isoformat()
    selected = _valid_date(d) or today
    log = db.get_log(selected)

    # プリフィル。旧チェックボックスで保存した日もタグとして復元する
    saved = (log["activity_tags"] or "").split(",") if log else []
    saved_tags = [t for t in (db.normalize_tag(t) for t in saved) if t]
    for tag in db.tags_from_flags(log):
        if tag not in saved_tags:
            saved_tags.append(tag)

    # ボタンで出すタグ（よく使う→登録済み→状態）。残りは自由記入欄に戻す
    frequent = db.frequent_tags()
    vocab = [t for t in db.ACTIVITY_TAGS if t not in frequent]
    buttoned = set(frequent) | set(vocab) | set(db.FLAG_TAGS)
    free_tags = [t for t in saved_tags if t not in buttoned]

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "today": today,
            "selected": selected,
            "log": log,
            "saved_tags": saved_tags,
            "free_tags": ",".join(free_tags),
            "recent": db.list_recent_logs(14),
            "warnings": _warnings(today),
            "mp_levels": db.MP_LEVELS,
            "wake_qualities": db.WAKE_QUALITIES,
            "frequent_tags": frequent,
            "tag_vocab": vocab,
            "flag_tags": db.FLAG_TAGS,
            "flag_icons": db.FLAG_TAG_ICONS,
        },
    )


@app.post("/logs")
def save_log(
    log_date: str = Form(...),
    mp_level: str = Form(""),
    wake_quality: str = Form(""),
    tags: list[str] = Form([]),
    tags_free: str = Form(""),
    note: str = Form(""),
):
    target = _valid_date(log_date) or date.today().isoformat()

    # ボタン＋自由記入をカンマ区切りに正規化（読点区切りも許容）
    tag_list: list[str] = []
    for raw in list(tags) + tags_free.replace("、", ",").split(","):
        t = db.normalize_tag(raw)
        if t and t not in tag_list:
            tag_list.append(t)

    # 没頭・散歩・苦しい夢・クラッシュはタグから立てる
    flags = db.flags_from_tags(tag_list)

    # 体重と没頭分数はフォームから外したので、既存の値をそのまま引き継ぐ。
    # 送信されない項目を None で上書きして過去の記録を消さないため。
    prev = db.get_log(target)

    db.upsert_log(
        log_date=target,
        mp_level=mp_level if mp_level in db.MP_LEVELS else None,
        wake_quality=wake_quality if wake_quality in db.WAKE_QUALITIES else None,
        bad_dream=flags["bad_dream"],
        hyperfocus=flags["hyperfocus"],
        hyperfocus_minutes=prev["hyperfocus_minutes"] if prev else None,
        weight_kg=prev["weight_kg"] if prev else None,
        walk=flags["walk"],
        activity_tags=",".join(tag_list) or None,
        crash=flags["crash"],
        note=note.strip() or None,
    )
    return RedirectResponse(url=f"/?d={target}", status_code=303)


@app.get("/calendar")
def calendar(request: Request, months: int = Query(4, ge=1, le=24)):
    return templates.TemplateResponse(
        request=request,
        name="calendar.html",
        context={"months": db.calendar_months(months), "mp_levels": db.MP_LEVELS},
    )


@app.get("/insights")
def insights(request: Request, weeks: int = Query(8, ge=1, le=52)):
    return templates.TemplateResponse(
        request=request,
        name="insights.html",
        context={
            "summary": db.weekly_summary(weeks),
            "wake": db.wake_vs_mp(),
            "tags": db.tag_vs_mp(),
            "weights": db.weight_series(),
            "stats": db.overall_stats(),
            "mp_levels": db.MP_LEVELS,
            "min_samples": db.MIN_SAMPLES,
            "max_mp": db.MP_LEVELS["excellent"]["value"],
        },
    )


@app.get("/api/logs")
def api_logs():
    return [dict(row) for row in db.list_all_logs()]
