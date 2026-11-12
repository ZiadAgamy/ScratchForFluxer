"""Schedule blocks (a time of day in a time zone, or every so often) and the clock block, run on a pretend clock."""

from __future__ import annotations

import asyncio
import datetime
import types
import zoneinfo

import bot_harness
import pytest
from blocks import block, hat, workspace

from scratch_fluxer import catalogue
from scratch_fluxer.compiler import compile_workspace

CAIRO = "Africa/Cairo"


def at_time(time="09:05", days="0123456", zone=CAIRO, *steps):
    return hat("sff_event_schedule", *steps, fields={"TIME": time, "DAYS": days, "ZONE": zone})


def every(count="30", unit="minutes", *steps):
    return hat("sff_event_interval", *steps, fields={"EVERY": count, "UNIT": unit})


def say(text="hello", channel="123"):
    return block("sff_send_channel", inputs={"TEXT": text, "CHANNEL": channel})


def build(tmp_path, *top):
    result = compile_workspace(workspace(*top))
    assert result.ok, result.diagnostics
    return bot_harness.start(result.code, tmp_path), result


class PretendClock:
    """Stands in for the ``datetime`` module inside the generated file: each look at the clock is the next moment."""

    def __init__(self, *moments: datetime.datetime) -> None:
        self.moments = list(moments)
        self.zones: list[str] = []
        self.datetime = self

    def now(self, zone):
        self.zones.append(zone.key)
        if not self.moments:
            raise asyncio.CancelledError  # the test's way of stopping the endless loop
        return self.moments.pop(0).replace(tzinfo=zone)


def moment(hour, minute, second=0, day=12):  # 12 November 2026 is a Thursday (weekday 3)
    return datetime.datetime(2026, 11, day, hour, minute, second)


def instant_sleep():
    count = {"n": 0}

    async def sleep(seconds):
        count["n"] += 1
        count["seconds"] = seconds

    return types.SimpleNamespace(sleep=sleep, create_task=asyncio.create_task), count


async def run_until_stopped(running, mode, days, at, zone, seconds, *, clock=None, limit=None):
    jobs = {"n": 0}

    async def job():
        jobs["n"] += 1

    running.namespace["asyncio"], sleeps = instant_sleep()
    if limit is not None:  # a pretend sleep that ends the loop after some turns
        inner = running.namespace["asyncio"].sleep

        async def sleep(seconds):
            await inner(seconds)
            if sleeps["n"] >= limit:
                raise asyncio.CancelledError

        running.namespace["asyncio"].sleep = sleep
    if clock is not None:
        running.namespace["datetime"] = clock
    with pytest.raises(asyncio.CancelledError):
        await running.namespace["keep_schedule"](job, mode, days, at, zone, seconds)
    return jobs["n"], sleeps


async def test_a_time_of_day_runs_once_in_that_minute_in_that_zone(tmp_path):
    running, _ = build(tmp_path, at_time("09:05", "0123456", CAIRO, say()))
    clock = PretendClock(moment(9, 4, 55), moment(9, 5, 5), moment(9, 5, 15), moment(9, 5, 55), moment(9, 6))
    runs, _ = await run_until_stopped(running, "time", "0123456", "09:05", CAIRO, 0, clock=clock)
    assert runs == 1
    assert set(clock.zones) == {CAIRO}


async def test_it_runs_again_the_next_day(tmp_path):
    running, _ = build(tmp_path, at_time("09:05", "0123456", CAIRO, say()))
    clock = PretendClock(moment(9, 5), moment(9, 6), moment(9, 5, day=13))
    runs, _ = await run_until_stopped(running, "time", "0123456", "09:05", CAIRO, 0, clock=clock)
    assert runs == 2


async def test_days_that_were_not_chosen_are_skipped(tmp_path):
    running, _ = build(tmp_path, at_time("09:05", "56", CAIRO, say()))
    clock = PretendClock(moment(9, 5))  # a Thursday, and the block is for the weekend
    runs, _ = await run_until_stopped(running, "time", "56", "09:05", CAIRO, 0, clock=clock)
    assert runs == 0
    clock = PretendClock(moment(9, 5, day=14))  # a Saturday
    runs, _ = await run_until_stopped(running, "time", "56", "09:05", CAIRO, 0, clock=clock)
    assert runs == 1


async def test_an_interval_runs_after_each_wait(tmp_path):
    running, _ = build(tmp_path, every("2", "hours", say()))
    runs, sleeps = await run_until_stopped(running, "every", "", "", "UTC", 7200, limit=3)
    assert runs == 2 and sleeps["seconds"] == 7200  # the third wait is cut short, so it never runs


async def test_a_job_that_fails_is_printed_and_the_schedule_goes_on(tmp_path, capsys):
    running, _ = build(tmp_path, every("1", "minutes", say()))
    running.namespace["asyncio"], sleeps = instant_sleep()
    calls = {"n": 0}

    async def job():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")

    inner = running.namespace["asyncio"].sleep

    async def sleep(seconds):
        await inner(seconds)
        if sleeps["n"] >= 5:
            raise asyncio.CancelledError

    running.namespace["asyncio"].sleep = sleep
    with pytest.raises(asyncio.CancelledError):
        await running.namespace["keep_schedule"](job, "every", "", "", "UTC", 60)
    assert calls["n"] >= 2
    assert "A scheduled block had a problem" in capsys.readouterr().out


async def test_the_schedules_start_once_when_the_bot_is_ready(tmp_path):
    running, _ = build(tmp_path, at_time("09:05", "0123456", CAIRO, say()), every("5", "minutes", say()))
    await running.ready()
    await running.ready()  # connecting again must not start them twice
    tasks = running.namespace["SCHEDULE_TASKS"]
    try:
        assert len(tasks) == 2
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_the_job_sends_what_the_blocks_say(tmp_path):
    running, _ = build(tmp_path, at_time("09:05", "0123456", CAIRO, say("Good morning!", "123")))
    await running.namespace["scheduled_job"]()
    assert running.texts == ["Good morning!"] and running.sent[0]["channel"] == "123"


async def test_the_clock_block_reads_the_time_in_the_chosen_zone(tmp_path):
    parts = ("time", "date", "weekday", "hour", "minute", "day", "month", "year")
    steps = [say(block("sff_time_now", fields={"PART": part, "ZONE": "Asia/Tokyo"})) for part in parts]
    running, _ = build(tmp_path, hat("sff_event_ready", *steps))
    running.namespace["datetime"] = PretendClock(*[moment(8, 7)] * len(parts))
    await running.ready()
    assert running.texts == ["08:07", "2026-11-12", "Thursday", "8", "7", "12", "11", "2026"]
    assert running.namespace["datetime"].zones == ["Asia/Tokyo"] * len(parts)


def test_every_zone_in_the_menu_is_a_real_time_zone():
    for label, name in catalogue.ZONE_OPTIONS:
        zoneinfo.ZoneInfo(name)
        assert label


def test_bad_times_and_intervals_are_warnings_that_fall_back():
    bad = compile_workspace(workspace(at_time("25:99", "0123456", CAIRO, say()), every("abc", "minutes", say())))
    assert bad.ok
    text = " ".join(d.message for d in bad.diagnostics)
    assert "09:00 is used" in text and "30 minutes is used" in text
    assert '"09:00"' in bad.code and ", 1800)" in bad.code
    too_often = compile_workspace(workspace(every("0", "minutes", say()), every("9999", "hours", say())))
    assert too_often.code.count(", 1800)") == 2


def test_a_time_is_written_with_two_digits():
    result = compile_workspace(workspace(at_time("9:05", "0123456", CAIRO, say())))
    assert '"09:05"' in result.code


def test_an_unknown_zone_or_day_falls_back_with_a_warning():
    result = compile_workspace(workspace(at_time("09:05", "xyz", "Mars/Base", say())))
    assert result.ok and '"UTC"' in result.code and '"0123456"' in result.code
    assert len([d for d in result.diagnostics if "isn't one of the choices" in d.message]) == 2


def test_only_ten_schedules_are_used():
    result = compile_workspace(workspace(*[every("5", "minutes", say()) for _ in range(11)]))
    assert result.ok and any("up to 10 schedule blocks" in d.message for d in result.diagnostics)
    assert result.code.count(', "every", ') == 10


def test_a_bot_without_schedules_has_none_of_the_schedule_code():
    result = compile_workspace(workspace(hat("sff_event_ready", say())))
    assert "keep_schedule" not in result.code and "zoneinfo" not in result.code
