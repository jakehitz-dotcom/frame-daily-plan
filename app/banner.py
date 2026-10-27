"""The message banner on top of the plan: a custom message, else a birthday, else an American
holiday greeting. From Dec 1 to 24 a Christmas countdown runs alongside (or alone)."""
import datetime

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


def for_day(cfg, today):
    """({'text': ..., 'source': ...} or None, days until Christmas or None)."""
    text, source = custom(cfg, today), 'custom'
    if not text and cfg.get('greetings', True):
        text, source = birthday(cfg, today), 'birthday'
        if not text:
            text, source = holidays(today.year).get(today), 'holiday'
    countdown = None
    if cfg.get('christmas_countdown', True) and today.month == 12 and today.day < 25:
        countdown = 25 - today.day
    return ({'text': text, 'source': source} if text else None), countdown
