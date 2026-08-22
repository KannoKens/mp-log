# -*- coding: utf-8 -*-
"""リマインダ（remind.py）のテスト。

外に出す副作用（PowerShell の起動）は全て差し替える。ここで守りたいのは
「**記録済みの日は何も言わない**」こと。毎朝シェルを開くたびに出る通知なので、
黙るべきときに黙らないと、そもそも使われなくなる。
"""

import subprocess
import sys
from datetime import datetime

import pytest

import remind


class _FrozenClock:
    """remind.datetime を差し替えて時刻を固定する。"""

    def __init__(self, moment):
        self._moment = moment

    def now(self):
        return self._moment


@pytest.fixture
def no_powershell(monkeypatch):
    """Windows 側を叩けない環境（CI の Linux ランナーもこれ）を再現する。"""
    monkeypatch.setattr(remind.shutil, "which", lambda _: None)


@pytest.fixture
def fake_powershell(monkeypatch):
    """PowerShell があることにして、終了コードだけ差し替える。"""
    def _fake(returncode=0, raises=None):
        monkeypatch.setattr(remind.shutil, "which", lambda _: "/fake/powershell.exe")

        calls = []

        def _run(cmd, **kwargs):
            calls.append(cmd)
            if raises is not None:
                raise raises
            return subprocess.CompletedProcess(cmd, returncode)

        monkeypatch.setattr(remind.subprocess, "run", _run)
        return calls
    return _fake


# ------------------------------------------------------------------ 文面の出し分け

def test_body_is_morning_before_noon(monkeypatch):
    monkeypatch.setattr(remind, "datetime", _FrozenClock(datetime(2026, 1, 1, 9)))
    assert remind._body() == remind.MORNING


def test_body_is_evening_from_noon(monkeypatch):
    monkeypatch.setattr(remind, "datetime", _FrozenClock(datetime(2026, 1, 1, 12)))
    assert remind._body() == remind.EVENING


# ------------------------------------------------------------------ 未記録の判定

def test_is_unrecorded_flips_once_the_day_is_saved(add_log, days_ago):
    day = days_ago(0)
    assert remind.is_unrecorded(day) is True
    add_log(day, "safe")
    assert remind.is_unrecorded(day) is False


def test_is_unrecorded_counts_a_blank_record_as_recorded(add_log, days_ago):
    """MP 未入力でも行があれば「記録済み」。促しの目的は入力の起点なので二度は言わない。"""
    day = days_ago(0)
    add_log(day, None)
    assert remind.is_unrecorded(day) is False


# ------------------------------------------------------------------ トースト通知

def test_toast_gives_up_quietly_outside_windows(no_powershell):
    assert remind.toast("題", "本文") is False


def test_toast_passes_an_encoded_command(fake_powershell):
    calls = fake_powershell(returncode=0)
    assert remind.toast("題", "本文") is True
    assert "-EncodedCommand" in calls[0], "日本語が壊れないよう UTF-16LE で渡す"
    assert "-NoProfile" in calls[0]


def test_toast_reports_failure_on_nonzero_exit(fake_powershell):
    fake_powershell(returncode=1)
    assert remind.toast("題", "本文") is False


@pytest.mark.parametrize(
    "error", [OSError("見つからない"), subprocess.TimeoutExpired("powershell.exe", 30)]
)
def test_toast_survives_a_broken_powershell(fake_powershell, error):
    fake_powershell(raises=error)
    assert remind.toast("題", "本文") is False


# ------------------------------------------------------------------ CLI

@pytest.fixture
def run_cli(monkeypatch):
    def _run(*args):
        monkeypatch.setattr(sys, "argv", ["remind.py", *args])
        return remind.main()
    return _run


def test_cli_says_nothing_when_the_day_is_recorded(run_cli, add_log, days_ago, capsys):
    add_log(days_ago(0), "safe")
    assert run_cli("--terminal") == 0
    assert capsys.readouterr().out == ""


def test_cli_prints_one_line_when_unrecorded(run_cli, days_ago, capsys):
    assert run_cli("--terminal") == 0
    out = capsys.readouterr().out
    assert days_ago(0) in out
    assert remind.URL in out
    assert out.count("\n") == 1, "シェル起動時の表示なので1行に収める"


def test_cli_defaults_to_terminal_mode(run_cli, capsys):
    assert run_cli() == 0
    assert "記録がまだです" in capsys.readouterr().out


def test_cli_toast_mode_fails_loudly_when_it_cannot_notify(run_cli, no_powershell):
    """systemd timer から見て、通知できなかったことが終了コードで分かること。"""
    assert run_cli("--toast") == 1


def test_cli_toast_mode_succeeds(run_cli, fake_powershell):
    fake_powershell(returncode=0)
    assert run_cli("--toast") == 0


def test_cli_test_mode_notifies_even_when_recorded(run_cli, fake_powershell,
                                                   add_log, days_ago):
    calls = fake_powershell(returncode=0)
    add_log(days_ago(0), "safe")
    assert run_cli("--test") == 0
    assert len(calls) == 1, "記録済みでも動作確認の通知は出す"
