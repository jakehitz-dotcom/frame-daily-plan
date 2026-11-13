"""The message banner on top of the plan: a custom message, else a birthday, else an American
holiday greeting. From Dec 1 to 24 a Christmas countdown runs alongside (or alone)."""
import datetime
import re

from dateutil.easter import easter


def _nth(year, month, weekday, n):
    """n-th weekday of a month (weekday: Mon=0); n=-1 for the last one."""
    if n > 0:
        first = datetime.date(year, month, 1)
        return first + datetime.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1) if month < 12 else datetime.date(year, 12, 31)
    return last - datetime.timedelta(days=(last.weekday() - weekday) % 7)


def holidays(year):
    """US federal holidays and widely celebrated observances → greeting. (Columbus Day is left out.)"""
    MON, THU, SUN = 0, 3, 6
    return {
        datetime.date(year, 1, 1): '🎉 Happy New Year! 🎉',
        _nth(year, 1, MON, 3): 'Honoring Dr. Martin Luther King Jr.',
        datetime.date(year, 2, 14): '❤️ Happy Valentine’s Day! ❤️',
        _nth(year, 2, MON, 3): '🇺🇸 Happy Presidents’ Day',
        datetime.date(year, 3, 17): '☘️ Happy St. Patrick’s Day! ☘️',
        easter(year): '🐣 Happy Easter! 🌷',
        _nth(year, 5, SUN, 2): '💐 Happy Mother’s Day! 💐',
        _nth(year, 5, MON, -1): '🇺🇸 Remembering the fallen this Memorial Day',
        datetime.date(year, 6, 19): '🎉 Happy Juneteenth!',
        _nth(year, 6, SUN, 3): '👔 Happy Father’s Day! 👔',
        datetime.date(year, 7, 4): '🎆 Happy Fourth of July! 🇺🇸',
        _nth(year, 9, MON, 1): '🛠️ Happy Labor Day!',
        datetime.date(year, 10, 31): '🎃 Happy Halloween! 👻',
        datetime.date(year, 11, 11): '🇺🇸 Thank you, veterans. Happy Veterans Day',
        _nth(year, 11, THU, 4): '🦃 Happy Thanksgiving! 🥧',
        datetime.date(year, 12, 24): '🎄 Merry Christmas Eve! ✨',
        datetime.date(year, 12, 25): '🎄 Merry Christmas! 🎁',
        datetime.date(year, 12, 31): '🥂 Happy New Year’s Eve! 🎉',
    }


def custom(cfg, today):
    b = cfg.get('banner') or {}
    text, until = (b.get('text') or '').strip(), (b.get('until') or '').strip()
    if text and (not until or until >= today.isoformat()):
        return text
    return None


def birthday(cfg, today):
    for b in cfg.get('birthdays') or []:
        if b.get('date') == today.strftime('%m-%d') and b.get('text'):
            return f"🎂 {b['text']} 🎈"
    return None


VISIT = re.compile(r"(?i)\bvisit(?:s|ing)?\b")


def visitor_name(title):
    """'Cheryl Visit' / "Cheryl's visit" / 'Visit from Cheryl & Bob' / 'Cheryl visiting' -> the name(s),
    or None when the title is not a visit."""
    if not VISIT.search(title or ''):
        return None
    name = VISIT.sub(' ', title)
    name = re.sub(r"(?i)\b(from|by|with|the|to us|here|trip)\b", ' ', name)
    name = re.sub(r"(?i)['’]s\b", ' ', name)
    name = re.sub(r'[\s:,\-–—!.]+', ' ', name).strip()
    return name or None


def visits(cals, today, tz):
    """('welcome' names, 'farewell' names) for multi-day visit events that start or end today."""
    import recurring_ical_events
    start = datetime.datetime.combine(today, datetime.time(), tz)
    welcome, farewell = [], []
    for cal in cals:
        try:
            evs = recurring_ical_events.of(cal).between(start, start + datetime.timedelta(days=1))
        except Exception:  # noqa: BLE001
            continue
        for ev in evs:
            name = visitor_name(' '.join(str(ev.get('SUMMARY', '')).split()))
            if not name:
                continue
            s = ev['DTSTART'].dt
            e = ev['DTEND'].dt if ev.get('DTEND') else s
            if isinstance(s, datetime.datetime):
                first = s.astimezone(tz).date()
                end = e.astimezone(tz)
                last = (end - datetime.timedelta(seconds=1)).date() if end.time() == datetime.time() else end.date()
            else:
                first, last = s, e - datetime.timedelta(days=1)   # all-day: DTEND is the day after
            if last <= first:
                continue  # one day only: not a stay
            if today == first and name not in welcome:
                welcome.append(name)
            elif today == last and name not in farewell:
                farewell.append(name)
    return welcome, farewell


def _names(names):
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' & ' + names[-1]


def visit_line(welcome, farewell):
    parts = []
    if welcome:
        parts.append(f'👋 Welcome {_names(welcome)}!')
    if farewell:
        parts.append(f'Farewell {_names(farewell)}! 💛')
    return '  ·  '.join(parts) or None


def for_day(cfg, today, visit=None):
    """({'text': ..., 'source': ...} or None, days until Christmas or None). visit: a welcome / farewell
    line for a guest arriving or leaving today, combined with any other greeting."""
    text, source = custom(cfg, today), 'custom'
    if not text and cfg.get('greetings', True):
        text, source = birthday(cfg, today), 'birthday'
        if not text:
            text, source = holidays(today.year).get(today), 'holiday'
    if visit and cfg.get('greetings', True):
        text, source = (f'{visit}  ·  {text}' if text else visit), (source + '+visit' if text else 'visit')
    countdown = None
    if cfg.get('christmas_countdown', True) and today.month == 12 and today.day < 25:
        countdown = 25 - today.day
    return ({'text': text, 'source': source} if text else None), countdown
