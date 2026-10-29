"""Render the daily plan for the Frame's Art Mode (3840x2160 JPEG).

Blurred, darkened background (normally the artwork the plan replaces) with calm
panels: date + month + to-dos, today, weather + tomorrow. No clock or news on
purpose: the image is static for hours and should never surprise anyone.
Fonts are Inter and Noto Color Emoji (both OFL, bundled in fonts/).
"""
import calendar
import datetime
import math
import os
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 3840, 2160
M = 180
CREAM = (255, 249, 238)
SOFT = (214, 210, 200)
DIM = (165, 162, 155)
ORANGE = (242, 150, 60)
HEAT = (240, 106, 90)
COOL = (110, 170, 245)
CLEAN = (120, 200, 150)
COLORS = {'green': (120, 200, 170), 'orange': ORANGE, 'blue': (120, 160, 220), 'red': (232, 112, 112), 'grey': (170, 170, 170)}
HERE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(HERE, 'fonts')
EMOJI = os.path.join(FONTS, 'NotoColorEmoji.ttf')  # colour bitmaps, only usable at size 109
LABELS = {
    'de': {'weekdays': ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag'],
           'months': ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September',
                      'Oktober', 'November', 'Dezember'],
           'initials': 'MDMDFSS', 'today': 'Heute', 'tomorrow': 'Morgen', 'todos': 'Zu erledigen',
           'no_todos': 'Nichts zu erledigen', 'no_events': 'Keine Termine', 'more': '… und weitere', 'more_n': '… und {} weitere', 'done_hidden': '({} weitere erledigte {})', 'task': 'Aufgabe', 'tasks': 'Aufgaben', 'open_hidden': '({} weitere offen{} nicht gezeigt)', 'and_done': ' · {} erledigt',
           'past': 'vorbei', 'night': 'bis {}° in der Nacht', 'all_day': 'Ganztägig',
           'date': '{day}. {month}', 'week': 'Nächste 7 Tage', 'wind': '{} km/h Böen', 'rain': '{} mm Regen',
           'inside': 'Drinnen', 'heat': 'Heizen auf', 'cool': 'Kühlen auf', 'heat_cool': 'Automatik',
           'hot_tub': 'Whirlpool', 'dishwasher': 'Spülmaschine', 'clean': 'Sauber',
           'bottle': 'Letzte Flasche {}', 'ago': 'vor {}', 'hilo': 'Hoch {}°  ·  Tief {}°',
           'uv_names': ['extrem', 'sehr hoch', 'hoch', 'mittel', 'niedrig'], 'uv_window': '{}–{}', 'uv_high': 'Hoher UV: ', 'uv_now': 'UV {} jetzt',
           'quiet': 'Ruhige Woche: kein Regen oder Sturm erwartet',
           'xmas_days': 'Tage bis Weihnachten', 'xmas_tomorrow': 'Morgen ist Weihnachten!',
           'week_ahead': 'Die kommende Woche', 'updated': 'aktualisiert {}', 'updated_at': 'Aktualisiert um {}', 'at_a_glance': 'Die Woche auf einen Blick',
           'nothing_planned': 'Nichts geplant'},
    'en': {'weekdays': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'],
           'months': ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
                      'October', 'November', 'December'],
           'initials': 'MTWTFSS', 'today': 'Today', 'tomorrow': 'Tomorrow', 'todos': 'To do',
           'no_todos': 'Nothing to do', 'no_events': 'No events', 'more': '… and more', 'more_n': '… and {} more', 'done_hidden': '({} additional completed {})', 'task': 'task', 'tasks': 'tasks', 'open_hidden': '({} more to do{} not shown)', 'and_done': ' · {} completed',
           'past': 'done', 'night': 'down to {}° tonight', 'all_day': 'All day',
           'date': '{month} {day}', 'week': 'Next 7 days', 'wind': '{} mph peak', 'rain': '{} in rain',
           'inside': 'Inside', 'heat': 'Heating to', 'cool': 'Cooling to', 'heat_cool': 'Auto',
           'hot_tub': 'Hot tub', 'dishwasher': 'Dishwasher', 'clean': 'Clean',
           'bottle': 'Last bottle {}', 'ago': '{} ago', 'hilo': 'High {}°  ·  Low {}°',
           'uv_names': ['extreme', 'very high', 'high', 'moderate', 'low'], 'uv_window': '{}–{}', 'uv_high': 'High UV: ', 'uv_now': 'UV {} now',
           'quiet': 'Quiet week ahead: no storms or rain expected',
           'xmas_days': 'days until Christmas', 'xmas_tomorrow': 'Christmas is tomorrow!',
           'week_ahead': 'The week ahead', 'updated': 'updated {}', 'updated_at': 'Updated at {}', 'at_a_glance': 'Week at a glance',
           'nothing_planned': 'Nothing planned'},
}
L = LABELS['de']  # set per render


_fonts = {}


def font(size, weight='Regular'):
    # Inter runs a little wider than the Avenir the layout was drawn with.
    key = (round(size * 0.94), weight)
    if key not in _fonts:
        name = {'Bold': 'Bold', 'DemiBold': 'SemiBold', 'Medium': 'Medium', 'Regular': 'Regular'}[weight]
        _fonts[key] = ImageFont.truetype(os.path.join(FONTS, f'Inter-{name}.ttf'), key[0])
    return _fonts[key]


def emoji(im, ch, x, y, size):
    """Noto Color Emoji only draws at 109 px, so draw there and scale (x, y = top left)."""
    tile = Image.new('RGBA', (140, 140), (0, 0, 0, 0))
    ImageDraw.Draw(tile).text((0, 0), ch, font=ImageFont.truetype(EMOJI, 109), embedded_color=True)
    tile = tile.crop(tile.getbbox() or (0, 0, 140, 140))
    scale = size / max(tile.size)
    tile = tile.resize((max(1, round(tile.width * scale)), max(1, round(tile.height * scale))), Image.LANCZOS)
    im.alpha_composite(tile, (round(x + (size - tile.width) / 2), round(y + (size - tile.height) / 2)))


UV_LEVELS = ((11, (190, 130, 230)), (8, HEAT), (6, ORANGE), (3, (235, 200, 90)), (0, CLEAN))


def fit_one_or_two(d, text, weight, width, size, one_line_smallest, two_line_smallest):
    """Prefer one line, shrinking a little; wrap to two lines only when it truly will not fit."""
    s, lines = fit_lines(d, text, weight, width, size, one_line_smallest, 1)
    if d.textlength(lines[0], font=font(s, weight)) <= width and len(wrap(d, text, font(s, weight), width, 10)) == 1:
        return s, lines
    return fit_lines(d, text, weight, width, size, two_line_smallest, 2)


def fit_lines(d, text, weight, width, size, smallest, max_lines=2):
    """The largest size (down to smallest) at which text fits in max_lines lines of width."""
    while True:
        f = font(size, weight)
        lines = wrap(d, text, f, width, max_lines=10)
        if (len(lines) <= max_lines and all(d.textlength(t, font=f) <= width for t in lines)) or size <= smallest:
            return size, lines[:max_lines]
        size -= 2


def weather_panel(im, d, wx, week, x, x1, top):
    """Now, high/low, condition, UV, what to wear, then the 7-day outlook. With im=None it only
    measures, so the panel can be sized to its content. Returns the panel's bottom edge."""
    w = x1 - x
    draw = im is not None
    if draw:
        emoji(im, wx['icon'], x, top + 60, 200)
        d.text((x + 250, top + 40), f"{wx.get('now', wx['max'])}°", font=font(150, 'DemiBold'), fill=CREAM)
        d.text((x + 254, top + 222), L['hilo'].format(wx['max'], wx['min']), font=font(56, 'Medium'), fill=SOFT)
    y = top + 305
    size, lines = fit_lines(d, wx['text'], 'Medium', w, 64, 48, 1)
    if draw:
        d.text((x, y), lines[0], font=font(size, 'Medium'), fill=CREAM)
    y += 88
    uv = wx.get('uv') or {}
    if uv.get('now') is not None or uv.get('from'):  # UV now, and today's hours above 4
        window = L['uv_window'].format(uv['from'], uv['to']) if uv.get('from') else ''
        if uv.get('now') is not None:
            colour = next(c for floor, c in UV_LEVELS if uv['now'] >= floor)
            head = L['uv_now'].format(uv['now'])
            tail = f"  ·  {L['uv_high']}{window}" if window else ''
        else:
            colour, head, tail = ORANGE, L['uv_high'], window
        if draw:
            hf, tf = font(58, 'DemiBold'), font(54, 'Medium')
            d.text((x, y), head, font=hf, fill=colour)
            d.text((x + d.textlength(head, font=hf), y + 3), tail, font=tf, fill=SOFT)
        y += 84
    if wx.get('hint'):
        size, lines = fit_one_or_two(d, wx['hint'], 'DemiBold', w, 58, 48, 54)
        for line in lines:
            if draw:
                d.text((x, y), line, font=font(size, 'DemiBold'), fill=ORANGE)
            y += round(size * 1.22)
    outlook = wx.get('outlook')
    if week or outlook:  # "Next 7 days ———" heads the outlook
        y += 22
        if draw:
            lf = font(48, 'Medium')
            d.text((x, y), L['week'], font=lf, fill=DIM)
            lx = x + d.textlength(L['week'], font=lf) + 30
            d.line([(lx, y + 32), (x1, y + 32)], fill=(64, 68, 78), width=3)
        y += 76
        if week:
            parts = [(icon, L[key].format(f"{week[key]:g}"), colour if week.get(key + '_alert') else CREAM)
                     for key, icon, colour in (('wind', '💨', HEAT), ('rain', '🌧️', COOL)) if key in week]
            size = 60
            while size > 44 and sum(80 + d.textlength(t, font=font(size, 'DemiBold')) for _, t, _ in parts) \
                    + 70 * (len(parts) - 1) > w:
                size -= 2
            if draw:
                cx = x
                for icon, text, colour in parts:
                    f = font(size, 'DemiBold')
                    emoji(im, icon, cx, y, 62)
                    d.text((cx + 80, y + (60 - size) // 2), text, font=f, fill=colour)
                    cx += 80 + d.textlength(text, font=f) + 70
            y += 86
        if outlook:
            quiet = outlook == L['quiet']
            size, lines = fit_one_or_two(d, outlook, 'Medium', w, 56, 46, 52)
            for line in lines:
                if draw:
                    d.text((x, y), line, font=font(size, 'Medium'), fill=DIM if quiet else CREAM)
                y += round(size * 1.22)
    return y + 40


TODO_FULL, TODO_SMALLEST = 56, 46   # text size of a to-do line (five rows fit), and the smallest it shrinks to
FOOTER = 62                          # the small "(… not shown)" line


def hint_lines(d, hint, room):
    """Two lines from the hint: split at '|'; else before a second 'Alexa,'; else wrapped to fit."""
    if '|' in hint:
        return [t.strip() for t in hint.split('|') if t.strip()][:2]
    second = hint.find('Alexa,', 1)
    if second > 0:
        return [hint[:second].strip(' ,·;'), hint[second:].strip()]
    if d.textlength(hint, font=font(40, 'Medium')) <= room:
        return [hint]
    return wrap(d, hint, font(34, 'Medium'), room, max_lines=2)


def todo_row(size):
    """Row height: airy at full size, tighter as the text shrinks for a long list."""
    return round(size * (1.56 if size >= 70 else 1.33))


def plan_todos(open_, done, height):
    """Which to-dos fit, at what size, and what the small footer says:
    1. all fit at full size; 2. otherwise completed ones are hidden and counted; 3. open ones shrink
    to fit; 4. at the smallest size, what still does not fit is counted too."""
    row = todo_row
    if (len(open_) + len(done)) * row(TODO_FULL) <= height:
        return TODO_FULL, open_, done, None
    room = height - (FOOTER if done else 0)
    size = TODO_FULL
    while size > TODO_SMALLEST and len(open_) * row(size) > room:
        size -= 2
    if len(open_) * row(size) <= room:
        # Rows left over after the open tasks go to the most recently completed ones; the rest are counted.
        shown = done[:max(0, room // row(size) - len(open_))]
        hidden = len(done) - len(shown)
        footer = L['done_hidden'].format(hidden, L['task'] if hidden == 1 else L['tasks']) if hidden else None
        return size, open_, shown, footer
    fits = max(1, (height - FOOTER) // row(TODO_SMALLEST))
    footer = L['open_hidden'].format(len(open_) - fits, L['and_done'].format(len(done)) if done else '')
    return TODO_SMALLEST, open_[:fits], [], footer


def todo_list(d, open_, done, x, x1, y, bottom):
    if not open_ and not done:
        d.text((x, y), L['no_todos'], font=font(70, 'Medium'), fill=DIM)
        return
    size, show_open, show_done, footer = plan_todos(open_, done, bottom - y)
    tx, max_w = x + round(size * 1.56), x1 - x - round(size * 1.56)
    while size > 48 and any(d.textlength(t, font=font(size, 'Medium')) > max_w for t in show_open + show_done):
        size -= 2  # one size for the whole list, shrunk until the longest item fits the panel
    row, box = todo_row(size), round(size * 1.06)
    f = font(size, 'Medium')
    for i, text in enumerate(show_open + show_done):
        is_done = i >= len(show_open)
        top = y + (row - box) // 2 - round(size * 0.12)
        if is_done:
            d.rounded_rectangle([x, top, x + box, top + box], radius=round(box * 0.21), fill=(150, 98, 52))
            d.line([(x + box * 0.24, top + box * 0.52), (x + box * 0.43, top + box * 0.70), (x + box * 0.77, top + box * 0.30)],
                   fill=CREAM, width=max(5, round(size * 0.1)), joint='curve')
        else:
            d.rounded_rectangle([x, top, x + box, top + box], radius=round(box * 0.21), outline=ORANGE,
                                width=max(6, round(size * 0.11)))
        ty = top + (box - size) // 2 - round(size * 0.08)
        d.text((tx, ty), text, font=f, fill=DIM if is_done else CREAM)
        if is_done:
            mid = ty + round(size * 0.62)
            d.line([(tx, mid), (tx + d.textlength(text, font=f), mid)], fill=DIM, width=max(3, round(size * 0.06)))
        y += row
    if footer:
        d.text((x, bottom - FOOTER + 8), footer, font=font(46, 'Medium'), fill=DIM)


def home_items(home):
    """(emoji, label, [(text, colour), ...]) for the Home bar, in reading order."""
    items = []
    if home.get('bottle'):
        b = home['bottle']
        items.append(('🍼', L['bottle'].format(b['at']), [(L['ago'].format(b['ago']), CREAM)]))
    if 'inside' in home:
        items.append(('🏠', L['inside'], [(f"{home['inside']}°", CREAM)]))
    s = home.get('set')
    if s:
        if s['mode'] == 'heat_cool':
            items.append(('🌡️', L['heat_cool'], [(f"{s['temps'][0]}°", HEAT), (' – ', SOFT), (f"{s['temps'][1]}°", COOL)]))
        else:
            items.append(('🔥' if s['mode'] == 'heat' else '❄️', L[s['mode']],
                          [(f"{s['temps'][0]}°", HEAT if s['mode'] == 'heat' else COOL)]))
    if 'hot_tub' in home:
        items.append(('♨️', L['hot_tub'], [(f"{home['hot_tub']}°", CREAM)]))
    if home.get('dishwasher_clean'):
        items.append(('🍽️', L['dishwasher'], [(L['clean'], CLEAN)]))
    return items


def home_bar(im, box, items):
    """One calm row along the bottom: icon, small label, big value, evenly spaced."""
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = box
    cell = (x1 - x0) / len(items)
    size = 88  # shrink together when many items share the bar, so none crowds its neighbour
    while size > 60 and any(130 + max(d.textlength(lb, font=font(round(size * 0.55), 'Medium')),
                                      sum(d.textlength(t, font=font(size, 'DemiBold')) for t, _ in pts)) > cell - 50
                            for _, lb, pts in items):
        size -= 4
    lf, vf = font(round(size * 0.55), 'Medium'), font(size, 'DemiBold')
    for i, (icon, label, parts) in enumerate(items):
        text_w = max(d.textlength(label, font=lf), sum(d.textlength(t, font=vf) for t, _ in parts))
        x = x0 + i * cell + (cell - 130 - text_w) / 2  # each item centred in its share of the bar
        emoji(im, icon, x, y0 + (y1 - y0 - 96) / 2, 96)
        d.text((x + 130, y0 + 18), label, font=lf, fill=DIM)
        tx = x + 130
        for text, colour in parts:
            d.text((tx, y0 + 64 + (88 - size) // 2), text, font=vf, fill=colour)
            tx += d.textlength(text, font=vf)


CLOCK_STYLES = ('ring', 'disc', 'minimal')


def clock_icon(size, hh, mm, style='ring', opacity=0.28):
    """A soft analog clock showing roughly hh:mm. Thick, round-ended hands and only twelve dots,
    so it gives the time at a glance but not the exact minute. Drawn 4x larger, then scaled down."""
    k = 4
    n = size * k
    ink = Image.new('L', (n, n), 0)          # coverage; colour and transparency are applied at the end
    d = ImageDraw.Draw(ink)
    c, r = n / 2, n / 2 - 4 * k
    if style == 'disc':
        d.ellipse([c - r, c - r, c + r, c + r], fill=70)
    elif style == 'ring':
        d.ellipse([c - r, c - r, c + r, c + r], outline=255, width=round(n * 0.03))
    for i in range(12):
        if style == 'minimal' and i % 3:
            continue
        a = math.radians(i * 30)
        big = i % 3 == 0
        dot = n * (0.036 if big else 0.018)
        x, y = c + r * 0.8 * math.sin(a), c - r * 0.8 * math.cos(a)
        d.ellipse([x - dot, y - dot, x + dot, y + dot], fill=255)

    def hand(angle, length, width):
        a = math.radians(angle)
        x, y = c + length * math.sin(a), c - length * math.cos(a)
        d.line([(c, c), (x, y)], fill=255, width=round(width))
        d.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=255)  # round end, no point
    hand((hh % 12) * 30 + mm * 0.5, r * 0.5, n * 0.075)
    hand(mm * 6, r * 0.74, n * 0.055)
    d.ellipse([c - n * 0.06, c - n * 0.06, c + n * 0.06, c + n * 0.06], fill=255)
    ink = ink.resize((size, size), Image.LANCZOS)
    out = Image.new('RGBA', (size, size), CREAM + (0,))
    out.putalpha(ink.point(lambda v: round(v * opacity)))
    return out


def wrap(d, text, f, max_w, max_lines=2):
    lines, line = [], ''
    for word in text.split(' '):  # split on plain spaces only: no-break spaces keep "16 Uhr" together
        if not word:
            continue
        trial = (line + ' ' + word).strip()
        if d.textlength(trial, font=f) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return lines[:max_lines]


def default_background():
    """A calm, generated backdrop (soft dusk gradient), so the project ships no third-party photo."""
    small = Image.new('RGB', (64, 36))
    corners = [(58, 74, 98), (92, 84, 110), (34, 52, 66), (70, 58, 72)]  # tl, tr, bl, br
    px = small.load()
    for y in range(36):
        v = y / 35
        for x in range(64):
            u = x / 63
            px[x, y] = tuple(round((corners[0][k] * (1 - u) + corners[1][k] * u) * (1 - v)
                                   + (corners[2][k] * (1 - u) + corners[3][k] * u) * v) for k in range(3))
    return small.resize((W, H), Image.BICUBIC)


def background(path):
    if not path:
        return Image.blend(default_background(), Image.new('RGB', (W, H), (12, 16, 22)), 0.25)
    im = Image.open(path).convert('RGB')
    w, h = im.size
    if w / h > W / H:
        nw = round(h * W / H); im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = round(w * H / W); im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    im = im.resize((W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(14))
    return Image.blend(im, Image.new('RGB', (W, H), (12, 16, 22)), 0.45)


def panel(layer, box, title=None):
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius=48, fill=(14, 18, 26, 150))
    if title:
        d.text((box[0] + 70, box[1] + 50), title, font=font(86, 'DemiBold'), fill=CREAM)


def events(d, items, x, y, w, bottom, scale=1.0):
    if not items:
        d.text((x, y), L['no_events'], font=font(70, 'Medium'), fill=DIM)
        return
    s = lambda v: round(v * scale)  # noqa: E731
    tf = font(s(74), 'DemiBold')
    for ev in items:
        lines = wrap(d, ev['title'], tf, w - 60)
        need = s(80) + len(lines) * s(92) + s(50)
        if y + need > bottom:
            d.text((x, y), L['more'], font=font(64, 'Medium'), fill=DIM)
            return
        past = ev.get('past')
        color = COLORS['grey'] if past else COLORS.get(ev.get('color', 'green'), COLORS['green'])
        d.rounded_rectangle([x, y + 8, x + 14, y + need - s(50)], radius=7, fill=color)
        times = ev.get('start', L['all_day']) + (f" – {ev['end']}" if ev.get('end') else '')
        d.text((x + 50, y), times + (f"  ·  {L['past']}" if past else ''), font=font(s(62), 'Medium'), fill=DIM if past else SOFT)
        for i, line in enumerate(lines):
            d.text((x + 50, y + s(80) + i * s(92)), line, font=tf, fill=DIM if past else CREAM)
        y += need


def month(d, date, x, y, w, row=86):
    d.text((x, y), L['months'][date.month - 1], font=font(72, 'DemiBold'), fill=CREAM)
    cw = w / 7
    y += 110 if row == 86 else 100
    for i, wd in enumerate(L['initials']):
        d.text((x + cw * i + cw / 2, y), wd, font=font(52, 'Medium'), fill=DIM, anchor='mt')
    y += 80 if row == 86 else 70
    for week in calendar.Calendar().monthdayscalendar(date.year, date.month):
        for i, day in enumerate(week):
            if not day:
                continue
            cx, cy = x + cw * i + cw / 2, y + 34
            if day == date.day:
                d.ellipse([cx - 46, cy - 46, cx + 46, cy + 46], fill=ORANGE)
            d.text((cx, cy), str(day), font=font(58, 'DemiBold' if day == date.day else 'Medium'),
                   fill=CREAM if day == date.day or i < 5 else SOFT, anchor='mm')
        y += row


def render(day, out, background_path=None):
    global L
    L = LABELS.get(day.get('lang', 'de'), LABELS['de'])
    date = datetime.date.fromisoformat(day['date'])
    bg = background(background_path).convert('RGBA')
    layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))

    c1, c2, c3 = (M, 1230), (1310, 2490), (2570, W - M)
    # Without a quote the panels use the strip at the bottom too.
    top, bottom = 560, 1880 if day.get('quote') else H - M
    items = home_items(day.get('home') or {})
    # The Home bar sits low, partly in the bottom margin, so the columns above lose as little as possible;
    # the month grid tightens to give the to-do list its rows back.
    bar = (M, bottom - 100, W - M, bottom + 70) if items and not day.get('quote') else None
    cal_bottom, todo_top = (1200, 1250) if bar else (1300, 1350)
    if bar:
        bottom = bar[1] - 50
    week = day.get('week') or {}
    wx = day.get('weather')
    scratch = ImageDraw.Draw(Image.new('RGB', (8, 8)))
    wx_bottom = weather_panel(None, scratch, wx, week, c3[0] + 70, c3[1] - 70, M) if wx else 820
    panel(layer, (c1[0], top, c1[1], cal_bottom))
    panel(layer, (c1[0], todo_top, c1[1], bottom), day.get('todo_title', L['todos']))
    panel(layer, (c2[0], M, c2[1], bottom), day.get('today_title', L['today']))
    panel(layer, (c3[0], M, c3[1], wx_bottom))
    panel(layer, (c3[0], wx_bottom + 50, c3[1], bottom), L['tomorrow'])
    if bar:
        panel(layer, bar)
    if day.get('clock'):  # behind the day of the week
        hh, mm = map(int, day['clock'].split(':'))
        size = 470  # large enough that the dots and hand tips reach past the letters
        icon = clock_icon(size, hh, mm, day.get('clock_style', 'ring'), day.get('clock_opacity', 0.28))
        layer.alpha_composite(icon, (c1[0] + 175 - size // 2, M + 130 - size // 2))
    # Text and icons go on their own transparent layer: combined with the panels by true alpha
    # compositing, the plan can be scaled down under a banner without fringes.
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    # Date, top left.
    d.text((c1[0] + 10, M - 20), L['weekdays'][date.weekday()], font=font(190, 'Bold'), fill=CREAM)
    d.text((c1[0] + 14, M + 220), L['date'].format(day=date.day, month=L['months'][date.month - 1]), font=font(96, 'Medium'), fill=SOFT)
    month(d, date, c1[0] + 70, top + 50, c1[1] - c1[0] - 140, row=74 if bar else 86)

    # A quiet reminder of how to add or tick off tasks, right of the title, on two lines.
    hint = (day.get('todo_hint') or '').strip()
    if hint:
        lines = hint_lines(d, hint, c1[1] - c1[0] - 140 - d.textlength(day.get('todo_title', L['todos']),
                                                                     font=font(86, 'DemiBold')) - 50)
        size = 36
        room = c1[1] - 70 - (c1[0] + 70 + d.textlength(day.get('todo_title', L['todos']), font=font(86, 'DemiBold')) + 50)
        while size > 28 and max(d.textlength(t, font=font(size, 'Medium')) for t in lines) > room:
            size -= 2
        y = todo_top + 48 + (2 - len(lines)) * size // 2
        for t in lines:
            d.text((c1[1] - 70, y), t, font=font(size, 'Medium'), fill=DIM, anchor='ra')
            y += round(size * 1.2)

    # To-dos: open ones first (empty boxes), then today's completed ones (ticked, struck through).
    todo_list(d, day.get('todos', []), day.get('todos_done', []), c1[0] + 70, c1[1] - 70, todo_top + 160, bottom - 40)

    events(d, day.get('today', []), c2[0] + 70, M + 200, c2[1] - c2[0] - 140, bottom - 40)

    if wx:
        weather_panel(im, d, wx, week, c3[0] + 70, c3[1] - 70, M)

    events(d, day.get('tomorrow', []), c3[0] + 70, wx_bottom + 215, c3[1] - c3[0] - 140, bottom - 40, scale=0.82)
    if bar:
        home_bar(im, bar, items)

    # Quote along the bottom.
    if day.get('quote'):
        d.text((W // 2, 2010), day['quote'], font=font(92, 'Medium'), fill=CREAM, anchor='mm')

    content = Image.alpha_composite(layer, im)
    if day.get('banner') or day.get('countdown') is not None:
        final = with_banner(bg, content, day.get('banner'), day.get('countdown'))
    else:
        final = Image.alpha_composite(bg, content)
    if day.get('updated'):  # barely there, at the very edge
        ImageDraw.Draw(final).text((26, H - 20), L['updated_at'].format(day['updated']), font=font(32, 'Medium'),
                                   fill=(112, 115, 122), anchor='ls')
    final.convert('RGB').save(out, quality=90)


# ── Banner across the top (custom message, birthday, holiday, Christmas countdown) ──────────────
EMOJI_RUN = re.compile('([\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2190-\u21FF\u2300-\u23FF]'
                       '[\uFE0F\u200D\U0001F3FB-\U0001F3FF\U0001F000-\U0001FAFF\u2600-\u27BF]*)')
FESTIVE_RED, FESTIVE_GREEN = (236, 96, 92), (110, 196, 140)
BANNER = (60, 220)                 # the banner's top and bottom edge
CONTENT = (140, 2090)              # the plan's used rows, scaled into the space below the banner


def rich_width(d, text, size, weight):
    return sum(size * 1.05 if EMOJI_RUN.fullmatch(p) else d.textlength(p, font=font(size, weight))
               for p in EMOJI_RUN.split(text) if p)


def draw_rich(im, d, text, x, y, size, weight, fill):
    """Text with emoji in it (Inter has none): words in the font, emoji as colour images."""
    for part in EMOJI_RUN.split(text):
        if not part:
            continue
        if EMOJI_RUN.fullmatch(part):
            emoji(im, part, x + size * 0.03, y + size * 0.08, size * 0.95)
            x += size * 1.05
        else:
            d.text((x, y), part, font=font(size, weight), fill=fill)
            x += d.textlength(part, font=font(size, weight))
    return x


def countdown_parts(days):
    if days == 1:
        return [('🎄 ', CREAM), (L['xmas_tomorrow'], FESTIVE_GREEN), (' 🎁', CREAM)]
    return [('🎄 ', CREAM), (str(days), FESTIVE_RED), (' ' + L['xmas_days'], FESTIVE_GREEN), (' 🎅', CREAM)]


def with_banner(bg, content, banner, countdown):
    """The banner strip on top, the whole plan scaled evenly into the space beneath it."""
    y0, y1 = BANNER
    gap, bottom = 40, H - 60
    s = (bottom - (y1 + gap)) / (CONTENT[1] - CONTENT[0])
    part = content.crop((0, CONTENT[0], W, CONTENT[1])).convert('RGBa')  # premultiplied: clean edges when scaled
    part = part.resize((round(W * s), round((CONTENT[1] - CONTENT[0]) * s)), Image.LANCZOS).convert('RGBA')
    canvas = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    canvas.paste(part, ((W - part.width) // 2, y1 + gap))
    x0, x1 = round((W - W * s) / 2 + M * s), round(W - (W - W * s) / 2 - M * s)   # line up with the panels below
    strip = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    panel(strip, (x0, y0, x1, y1))
    out = Image.alpha_composite(Image.alpha_composite(bg, strip), canvas)
    txt = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    cd = countdown_parts(countdown) if countdown is not None else None
    cd_size = 60
    cd_w = sum(rich_width(d, t, cd_size, 'DemiBold') for t, _ in cd) if cd else 0
    if banner and banner.get('text'):
        room = x1 - x0 - 140 - (cd_w + 80 if cd else 0)
        size = 92
        while size > 52 and rich_width(d, banner['text'], size, 'DemiBold') > room:
            size -= 4
        bw = rich_width(d, banner['text'], size, 'DemiBold')
        bx = x0 + 70 + (room - bw) / 2 if not cd else x0 + 70
        draw_rich(txt, d, banner['text'], bx, (y0 + y1) / 2 - size * 0.62, size, 'DemiBold', CREAM)
        if cd:
            cx = x1 - 70 - cd_w
            for t, colour in cd:
                cx = draw_rich(txt, d, t, cx, (y0 + y1) / 2 - cd_size * 0.62, cd_size, 'DemiBold', colour)
    elif cd:  # the countdown alone takes the banner
        size = 84
        width = sum(rich_width(d, t, size, 'DemiBold') for t, _ in cd)
        cx = (W - width) / 2
        for t, colour in cd:
            cx = draw_rich(txt, d, t, cx, (y0 + y1) / 2 - size * 0.62, size, 'DemiBold', colour)
    return Image.alpha_composite(out, txt)


def fit_artwork(src, out, mode='crop'):
    """Bring any picture to the Frame's 3840x2160.

    crop: fill the screen, trimming the overflow evenly (best for photos close to 16:9).
    fit:  show the whole picture, on a blurred, darkened copy of itself (portraits, panoramas).
    """
    from PIL import ImageOps
    im = ImageOps.exif_transpose(Image.open(src)).convert('RGB')  # phone photos carry their rotation in EXIF
    if mode == 'crop':
        im = ImageOps.fit(im, (W, H), Image.LANCZOS)
    else:
        bg = ImageOps.fit(im, (W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
        bg = Image.blend(bg, Image.new('RGB', (W, H), (12, 16, 22)), 0.35)
        scale = min(W / im.width, H / im.height)  # up or down, so small pictures fill the height too
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
        bg.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
        im = bg
    im.save(out, quality=92)


TEXT_THEMES = {'dark': ((24, 28, 36), CREAM), 'light': ((244, 239, 228), (38, 44, 56))}


def render_text(text, out, theme='dark'):
    """A message on a plain, calm background: large, centred, nothing else."""
    bg, ink = TEXT_THEMES.get(theme, TEXT_THEMES['dark'])
    im = Image.new('RGB', (W, H), bg)
    d = ImageDraw.Draw(im)
    import re
    # Never break between a number and its unit ("16 Uhr", "5 €"), or after a lone word like "um".
    text = re.sub(r'(\d+)\s+(Uhr|€|Euro|Minuten|min)\b', '\\1\u00a0\\2', text)
    paragraphs = [p.strip() for p in text.strip().splitlines()]
    max_w, max_h = W - 2 * 400, H - 2 * 300
    size = 260
    while size > 80:
        f = font(size, 'DemiBold')
        lines = []
        for p in paragraphs:
            lines += wrap(d, p, f, max_w, max_lines=20) if p else ['']
        line_h = round(size * 1.3)
        if len(lines) * line_h <= max_h and all(d.textlength(l, font=f) <= max_w for l in lines):
            break
        size -= 10
    y = (H - len(lines) * line_h) // 2 + line_h // 2
    for line in lines:
        d.text((W // 2, y), line, font=f, fill=ink, anchor='mm')
        y += line_h
    im.save(out, quality=92)


# ── The week ahead (Sunday evenings) ───────────────────────────────────────────────────────────
RAIN_BLUE = (120, 170, 245)
PILL = ((70, 62, 44, 230), (245, 205, 140))


def week_range(start, end):
    s, e = datetime.date.fromisoformat(start), datetime.date.fromisoformat(end)
    months = LABELS['en']['months']
    if s.month == e.month:
        return f'{months[s.month - 1]} {s.day} – {e.day}'
    return f'{months[s.month - 1][:3]} {s.day} – {months[e.month - 1][:3]} {e.day}'


def week_day_column(layer, txt, d, day, x0, x1, top, bottom):
    ld = ImageDraw.Draw(layer)
    panel(layer, (round(x0), top, round(x1), bottom))
    x, w = x0 + 40, x1 - x0 - 80
    d.text((x, top + 40), day['weekday'], font=font(74, 'DemiBold'), fill=CREAM)
    d.text((x1 - 40, top + 40), str(day['day']), font=font(74, 'DemiBold'), fill=SOFT, anchor='ra')
    y = top + 150
    wx = day.get('weather')
    if wx:
        emoji(txt, wx['icon'], x, y, 92)
        hf = font(70, 'DemiBold')
        d.text((x + 110, y - 4), f"{wx['hi']}°", font=hf, fill=CREAM)
        if wx.get('lo') is not None:
            d.text((x + 110 + d.textlength(f"{wx['hi']}°", font=hf) + 14, y + 16), f"{wx['lo']}°", font=font(50, 'Medium'), fill=DIM)
        wet = (wx.get('pop') or 0) >= 30
        text = wx['text'] + (f" · {wx['pop']}%" if wet else '')
        size, lines = fit_lines(d, text, 'Medium', w, 44, 36, 1)
        d.text((x, y + 108), lines[0], font=font(size, 'Medium'), fill=RAIN_BLUE if wet else SOFT)
    y += 185
    ld.line([(x, y), (x + w, y)], fill=(64, 68, 78, 255), width=3)
    y += 26
    for pill in day.get('pills', [])[:2]:
        pf = font(42, 'DemiBold')
        size, lines = fit_lines(d, pill, 'DemiBold', w - 36, 42, 34, 1)
        pw = rich_width(d, lines[0], size, 'DemiBold') + 36
        ld.rounded_rectangle((round(x), y, round(x + min(pw, w)), y + 62), radius=31, fill=PILL[0])
        draw_rich(txt, d, lines[0], x + 18, y + 8 + (42 - size) // 2, size, 'DemiBold', PILL[1])
        y += 80
    events = day.get('events', [])
    tf = font(50, 'DemiBold')
    for k, ev in enumerate(events):
        lines = wrap(d, ev['title'], tf, w - 30)
        need = 54 + len(lines) * 60 + 26
        if y + need > bottom - 30 - (60 if k < len(events) - 1 else 0):
            d.text((x, y), f'+{len(events) - k} more', font=font(46, 'Medium'), fill=DIM)
            return
        ld.rounded_rectangle((round(x), y + 6, round(x) + 9, y + need - 26), radius=4,
                             fill=COLORS.get(ev.get('color', 'green'), COLORS['green']) + (255,))
        d.text((x + 28, y), ev['start'], font=font(42, 'Medium'), fill=SOFT)
        for i, line in enumerate(lines):
            d.text((x + 28, y + 52 + i * 60), line, font=tf, fill=CREAM)
        y += need
    if not events and not day.get('pills'):
        d.text((x, y), L['nothing_planned'], font=font(46, 'Medium'), fill=DIM)


def render_week(week, out, background_path=None):
    """Seven columns, Monday to Sunday, and a short 'week at a glance' underneath."""
    global L
    L = LABELS.get(week.get('lang', 'en'), LABELS['en'])
    bg = background(background_path).convert('RGBA')
    layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    txt = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    d.text((M + 10, M - 40), L['week_ahead'], font=font(150, 'Bold'), fill=CREAM)
    d.text((M + 14, M + 150), week_range(week['start'], week['end']), font=font(80, 'Medium'), fill=SOFT)
    d.text((W - M, M + 170), L['updated'].format(week['updated']), font=font(50, 'Medium'), fill=DIM, anchor='rs')
    summary = week.get('summary') or []
    rows = (len(summary) + 1) // 2
    gap, top = 28, 450
    bottom = H - 90 - (150 + rows * 78 + 40 if summary else 0) - (50 if summary else 0)
    cw = (W - 2 * M - 6 * gap) / 7
    for i, day in enumerate(week['days']):
        x0 = M + i * (cw + gap)
        week_day_column(layer, txt, d, day, x0, x0 + cw, top, bottom)
    if summary:
        sy0 = bottom + 50
        panel(layer, (M, sy0, W - M, H - 90))
        d.text((M + 70, sy0 + 40), L['at_a_glance'], font=font(64, 'DemiBold'), fill=CREAM)
        colw = (W - 2 * M - 140 - 80) / 2
        for k, (icon, text) in enumerate(summary):
            cx = M + 70 + (k % 2) * (colw + 80)
            cy = sy0 + 140 + (k // 2) * 78
            emoji(txt, icon, cx, cy + 2, 54)
            size, lines = fit_lines(d, text, 'Medium', colw - 80, 48, 40, 1)
            line = lines[0] if lines[0] == text else lines[0].rstrip(' ,·') + '…'
            d.text((cx + 76, cy + (48 - size) // 2), line, font=font(size, 'Medium'), fill=SOFT)
    Image.alpha_composite(Image.alpha_composite(bg, layer), txt).convert('RGB').save(out, quality=90)


# ── A recipe shared from Paprika, full screen ──────────────────────────────────────────────────
def _rich_lines(d, tokens, width, size):
    """Greedy word wrap of [(word, bold)] at one size; returns lines of [(word, bold)]."""
    fonts = {False: font(size, 'Medium'), True: font(size, 'DemiBold')}
    space = d.textlength(' ', font=fonts[False])
    lines, line, x = [], [], 0.0
    for word, bold in tokens:
        w = d.textlength(word, font=fonts[bold])
        if line and x + space + w > width:
            lines.append(line)
            line, x = [], 0.0
        x += (space if line else 0) + w
        line.append((word, bold))
    if line:
        lines.append(line)
    return lines


def _tokens(text, bold_prefix=''):
    return [(w, True) for w in bold_prefix.split()] + [(w, False) for w in text.split()]


def _list_height(d, items, width, size, gap):
    return sum(len(_rich_lines(d, t, width, size)) * round(size * 1.3) + gap for t in items) - gap


def _fit_list(d, items, width, height, start, smallest, gap_ratio=0.45):
    size = start
    while size > smallest and _list_height(d, items, width, size, round(size * gap_ratio)) > height:
        size -= 2
    return size


def _draw_rich(d, line, x, y, size, colours=(CREAM, CREAM)):
    fonts = {False: font(size, 'Medium'), True: font(size, 'DemiBold')}
    space = d.textlength(' ', font=fonts[False])
    for k, (word, bold) in enumerate(line):
        d.text((x, y), word, font=fonts[bold], fill=colours[bold])
        x += d.textlength(word, font=fonts[bold]) + space


def render_recipe(rec, out, background_path=None, photo_path=None, until=None):
    """Title and times on top; photo and ingredients on the left; numbered steps on the right,
    in one or two columns, sized to fit the screen."""
    import recipe as recipe_mod
    global L
    L = LABELS['en']
    bg = background(background_path).convert('RGBA')
    bg = Image.blend(bg, Image.new('RGBA', (W, H), (10, 13, 18, 255)), 0.35)
    layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    txt = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)

    # Header: name, then times and servings
    name_size, lines = fit_lines(d, rec.get('name') or 'Recipe', 'Bold', W - 2 * M - 700, 130, 84, 1)
    d.text((M, M - 30), lines[0], font=font(name_size, 'Bold'), fill=CREAM)
    meta = rec.get('meta') or {}
    bits = [f'{label} {meta[k]}' for k, label in (('prep', 'Prep'), ('cook', 'Cook'), ('total', 'Total')) if meta.get(k)]
    if meta.get('servings'):
        bits.append(f"Serves {meta['servings']}")
    if bits:
        d.text((M + 4, M + 145), '  ·  '.join(bits), font=font(62, 'Medium'), fill=SOFT)
    if until:
        d.text((W - M, M + 10), f'Showing until {until}', font=font(46, 'Medium'), fill=DIM, anchor='ra')
    source = rec.get('source') or ''
    if source:
        host = re.sub(r'^www\.', '', urllib_host(source))
        d.text((W - M, M + 80), host, font=font(46, 'Medium'), fill=DIM, anchor='ra')

    top, bottom = M + 290, H - 110
    split = 1700
    # Left: photo, then ingredients
    panel(layer, (M, top, split, bottom))
    x0, x1 = M + 60, split - 60
    y = top + 50
    if photo_path:
        try:
            from PIL import ImageOps
            ph = ImageOps.fit(Image.open(photo_path).convert('RGB'), (x1 - x0, 440), Image.LANCZOS)
            mask = Image.new('L', ph.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, ph.width, ph.height), radius=36, fill=255)
            layer.paste(ph, (x0, y), mask)
            y += 440 + 50
        except Exception:  # noqa: BLE001  a bad image: carry on without it
            pass
    d.text((x0, y), 'Ingredients', font=font(72, 'DemiBold'), fill=CREAM)
    y += 115
    items, headings = [], {}
    for ing in rec.get('ingredients') or []:
        if isinstance(ing, dict):
            headings[len(items)] = ing['heading']
            items.append([(w, True) for w in ing['heading'].split()])
        else:
            qty, rest = recipe_mod.split_quantity(ing)
            items.append(_tokens(rest, qty))
    width = x1 - x0 - 50
    size = _fit_list(d, items, width, bottom - 50 - y, 60, 36, 0.3)
    gap = round(size * 0.3)
    for k, toks in enumerate(items):
        lines = _rich_lines(d, toks, width, size)
        if k in headings:
            for line in lines:
                _draw_rich(d, line, x0, y, size, (ORANGE, ORANGE))
                y += round(size * 1.3)
        else:
            r = size * 0.13
            d.ellipse((x0 + 8, y + size * 0.55 - r, x0 + 8 + 2 * r, y + size * 0.55 + r), fill=ORANGE)
            for line in lines:
                _draw_rich(d, line, x0 + 50, y, size, (SOFT, CREAM))
                y += round(size * 1.3)
        y += gap

    # Right: numbered steps, one column or two
    panel(layer, (split + 60, top, W - M, bottom))
    sx0, sx1 = split + 120, W - M - 60
    d.text((sx0, top + 50), 'Steps', font=font(72, 'DemiBold'), fill=CREAM)
    steps = [_tokens(s) for s in rec.get('steps') or []]
    sy, height = top + 165, bottom - 50 - (top + 165)
    full = sx1 - sx0
    num_w = 100
    one = _fit_list(d, steps, full - num_w, height, 62, 30, 0.6)
    cols = 1
    if one < 46 and len(steps) > 1:  # too small in one column: two columns read better
        cols = 2
    col_w = (full - 80) / cols if cols == 2 else full
    size = one if cols == 1 else 62
    if cols == 2:
        size = 62
        while size > 30:
            gap = round(size * 0.6)
            heights = [len(_rich_lines(d, t, col_w - num_w, size)) * round(size * 1.3) + gap for t in steps]
            # fill column 1 until full, rest to column 2
            col, used, ok = 0, 0, True
            for h_ in heights:
                if used + h_ - gap > height:
                    col, used = col + 1, 0
                    if col > 1:
                        ok = False
                        break
                used += h_
            if ok:
                break
            size -= 2
    gap = round(size * 0.6)
    col, cy = 0, sy
    for n, toks in enumerate(steps, 1):
        lines = _rich_lines(d, toks, col_w - num_w, size)
        h_ = len(lines) * round(size * 1.3)
        if cols == 2 and col == 0 and cy + h_ > sy + height:
            col, cy = 1, sy
        cx = sx0 + col * (col_w + 80)
        rr = round(size * 0.62)
        d.ellipse((cx, cy + size * 0.62 - rr, cx + 2 * rr, cy + size * 0.62 + rr), fill=ORANGE)
        d.text((cx + rr, cy + size * 0.62), str(n), font=font(round(size * 0.8), 'Bold'), fill=(20, 22, 28), anchor='mm')
        for line in lines:
            _draw_rich(d, line, cx + num_w, cy, size)
            cy += round(size * 1.3)
        cy += gap
    if rec.get('notes'):
        nsize, nlines = fit_lines(d, 'Note: ' + rec['notes'], 'Medium', full, 44, 34, 2)
        ny = bottom - 50 - len(nlines) * round(nsize * 1.3)
        if ny > cy:
            for line in nlines:
                d.text((sx0, ny), line, font=font(nsize, 'Medium'), fill=DIM)
                ny += round(nsize * 1.3)
    Image.alpha_composite(Image.alpha_composite(bg, layer), txt).convert('RGB').save(out, quality=90)


def urllib_host(url):
    import urllib.parse
    return urllib.parse.urlparse(url).netloc
