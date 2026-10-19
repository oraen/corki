"""Hermes-style elapsed intervals, one-shots and timezone-aware wall-clock cron."""

import math
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter


def parse(expression: str, timezone: str, now: float, *, kind: str | None = None) -> dict:
    if not isinstance(expression, str) or not 0 < len(expression.strip()) <= 120:
        raise ValueError("请填写有效的执行时间")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
        raise ValueError("请填写有效时区，例如 Asia/Shanghai") from error
    expression = expression.strip()
    if kind == "once":
        return parse_once(expression, zone, now)
    match = re.fullmatch(r"(?:(every|in)\s+)?(\d+)\s*([mhd])", expression.lower())
    if match:
        prefix, number, unit = match.groups()
        seconds = int(number) * {"m": 60, "h": 3600, "d": 86400}[unit]
        if not 60 <= seconds <= 366 * 86400:
            raise ValueError("执行间隔须在 1 分钟到 366 天之间")
        if prefix == "in":
            return {"kind": "once", "at": now + seconds}
        return {"kind": "interval", "seconds": seconds}
    if len(expression.split()) == 5:
        if not croniter.is_valid(expression):
            raise ValueError("Cron 表达式无效，请使用五个字段")
        result = {"kind": "cron", "expression": expression, "timezone": timezone}
        following(result, now)  # Reject valid syntax with no reachable calendar date.
        return result
    return parse_once(expression, zone, now)


def parse_once(expression: str, zone: ZoneInfo, now: float) -> dict:
    """Unambiguous year-first dates or Unix seconds/milliseconds; never fuzzy-guess dates."""
    expression = expression.strip()
    # In an explicitly one-shot form a duration means delay, not a recurring interval.
    delay = re.fullmatch(r"(?:in\s+)?(\d+)\s*(m|h|d|分钟|小时|天)(?:后)?", expression.lower())
    if delay:
        number, unit = delay.groups()
        seconds = (
            int(number)
            * {"m": 60, "h": 3600, "d": 86400, "分钟": 60, "小时": 3600, "天": 86400}[unit]
        )
        if not 60 <= seconds <= 366 * 86400:
            raise ValueError("延迟须在 1 分钟到 366 天之间")
        return {"kind": "once", "at": now + seconds}
    try:
        if re.fullmatch(r"\d{10}(?:\.\d{1,6})?", expression):
            value = datetime.fromtimestamp(float(expression), UTC)
        elif re.fullmatch(r"\d{13}", expression):
            value = datetime.fromtimestamp(int(expression) / 1000, UTC)
        else:
            relative = re.fullmatch(r"(今天|明天|后天)\s*(\d{1,2}:\d{2}(?::\d{2})?)", expression)
            if relative:
                day = datetime.fromtimestamp(now, zone).date() + timedelta(
                    days={"今天": 0, "明天": 1, "后天": 2}[relative[1]]
                )
                expression = f"{day.isoformat()} {relative[2]}"
            elif re.fullmatch(r"\d{14}", expression):
                expression = datetime.strptime(expression, "%Y%m%d%H%M%S").isoformat()
            # Support common separators and Chinese calendar notation, retaining explicit offsets.
            expression = re.sub(
                r"^(\d{4})[年/.](\d{1,2})[月/.](\d{1,2})日?", r"\1-\2-\3", expression
            )
            expression = expression.replace("时", ":").replace("分", ":").replace("秒", "")
            expression = expression.rstrip(":")
            date = re.fullmatch(
                r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[Tt\s]+(\d{1,2}):(\d{1,2})"
                r"(?::(\d{1,2})(\.\d{1,6})?)?\s*(Z|z|[+-]\d{2}:?\d{2})?)?",
                expression,
            )
            if date:
                year, month, day, hour, minute, second, fraction, offset = date.groups()
                expression = f"{int(year):04}-{int(month):02}-{int(day):02}"
                if hour is not None:
                    expression += (
                        f"T{int(hour):02}:{int(minute):02}:{int(second or 0):02}"
                        f"{fraction or ''}{(offset or '').upper()}"
                    )
            value = datetime.fromisoformat(expression)
        if value.tzinfo is None:
            value = value.replace(tzinfo=zone)
            if datetime.fromtimestamp(value.timestamp(), zone).replace(
                tzinfo=None
            ) != value.replace(tzinfo=None):
                raise ValueError("time falls in a DST gap")
        stamp = value.timestamp()
    except (ValueError, OverflowError, OSError) as error:
        raise ValueError("时间格式无效，请填写日期时间、10 位秒时间戳或 13 位毫秒时间戳") from error
    if not math.isfinite(stamp) or stamp <= now:
        raise ValueError("执行时间必须晚于当前时间")
    return {"kind": "once", "at": stamp}


def following(schedule: dict, after: float, anchor: float | None = None) -> float | None:
    kind = schedule["kind"]
    if kind == "once":
        return None
    if kind == "interval":
        seconds = schedule["seconds"]
        base = after if anchor is None else anchor
        return base + (max(0, math.floor((after - base) / seconds)) + 1) * seconds
    # Match Hermes: calendar cron uses the IANA wall clock, intervals use elapsed UTC time.
    zone = ZoneInfo(schedule["timezone"])
    wall = datetime.fromtimestamp(after, UTC).astimezone(zone).replace(tzinfo=None)
    iterator = croniter(schedule["expression"], wall, max_years_between_matches=8)
    try:
        for _ in range(180):
            candidate = iterator.get_next(datetime)
            for fold in (0, 1):
                aware = candidate.replace(tzinfo=zone, fold=fold)
                stamp = aware.timestamp()
                # Skip nonexistent spring-forward times; never return a past instant.
                if (
                    stamp > after
                    and datetime.fromtimestamp(stamp, zone).replace(tzinfo=None) == candidate
                ):
                    return stamp
    except (ValueError, OverflowError) as error:
        raise ValueError("Cron 在未来八年没有有效执行时间") from error
    raise ValueError("Cron 无法计算下次执行时间")
