"""Recipes shared from Paprika ("Recipe Text") to show full-screen on the Frame.

Paprika's text looks like:
    Red Curry Chicken and Rice Bowls

    Prep Time: 10 min | Cook Time: 50 min | Total Time: 1 hr | Servings: 4 servings

    Ingredients:
    ...one per line, a line ending in ':' is a group heading...

    Directions:
    ...one step per line...

    Notes: / Nutrition: / Source: <url>
"""
import html
import os
import re
import urllib.parse

import requests

SECTIONS = ('ingredients', 'directions', 'notes', 'nutrition', 'source', 'description')
META_KEYS = {'prep time': 'prep', 'cook time': 'cook', 'total time': 'total', 'servings': 'servings',
             'difficulty': 'difficulty', 'rating': 'rating'}


def looks_like_recipe(text):
    t = (text or '').lower()
    return 'ingredients:' in t and ('directions:' in t or 'instructions:' in t)


def parse(text):
    """{'name', 'meta': {prep, cook, total, servings}, 'ingredients': [str or {'heading': str}],
    'steps': [str], 'notes': str, 'source': url or ''}"""
    lines = [ln.rstrip() for ln in (text or '').replace('\r', '').split('\n')]
    out = {'name': '', 'meta': {}, 'ingredients': [], 'steps': [], 'notes': '', 'source': ''}
    section = None
    notes = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        head = line.rstrip(':').strip().lower()
        if line.endswith(':') and head in SECTIONS + ('instructions', 'method'):
            section = 'directions' if head in ('instructions', 'method') else head
            continue
        m = re.match(r'(?i)^source:\s*(\S+)', line)
        if m:
            out['source'] = m.group(1)
            section = 'source'
            continue
        if section is None:
            if not out['name']:
                out['name'] = line
                continue
            meta = dict((k.strip().lower(), v.strip()) for k, _, v in
                        (part.partition(':') for part in line.split('|')) if v)
            if meta and all(k in META_KEYS for k in meta):
                for k, v in meta.items():
                    out['meta'][META_KEYS[k]] = re.sub(r'(?i)\s*servings?$', '', v) if k == 'servings' else v
            continue
        if section == 'ingredients':
            if line.endswith(':') and len(line) < 60:
                out['ingredients'].append({'heading': line.rstrip(':')})
            else:
                out['ingredients'].append(line)
        elif section == 'directions':
            out['steps'].append(re.sub(r'^(?:step\s*)?\d+[.)]\s+', '', line, flags=re.I))
        elif section == 'notes':
            notes.append(line)
    out['notes'] = ' '.join(notes)
    return out


_NUM = r'[\d½⅓⅔¼¾⅛⅜⅝⅞]+(?:[/.][\d]+)?(?:\s+[\d]+/[\d]+|[½⅓⅔¼¾⅛⅜⅝⅞])?'
_UNIT = (r'(?:cups?|tablespoons?|tbsps?|teaspoons?|tsps?|pounds?|lbs?|ounces?|oz|grams?|g|kg|ml|l|liters?|'
         r'quarts?|pints?|cloves?|cans?|pinch(?:es)?|dash(?:es)?|sticks?|large|medium|small)\b\.?')
_AMOUNT = rf'{_NUM}(?:\s*{_UNIT})?'
QTY = re.compile(rf'^({_AMOUNT}(?:\s*(?:to|or|-|–)\s*{_AMOUNT})?)', re.I)


def split_quantity(ingredient):
    """('1½ cups', 'long-grain white rice') — the amount is drawn in bold."""
    m = QTY.match(ingredient)
    if not m or not m.group(1).strip():
        return '', ingredient
    return m.group(1).strip(), ingredient[m.end():].strip(' ,')


def fetch_photo(url, dest, timeout=8):
    """The page's main image (og:image / twitter:image) saved to dest, or None."""
    if not url or not url.startswith('http'):
        return None
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (frame-daily-plan)', 'Accept': 'text/html'}
        page = requests.get(url, headers=headers, timeout=timeout)
        page.raise_for_status()
        m = re.search(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*content=["\']([^"\']+)',
                      page.text[:300_000], re.I) or \
            re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\'](?:og:image|twitter:image)',
                      page.text[:300_000], re.I)
        if not m:
            return None
        img_url = urllib.parse.urljoin(url, html.unescape(m.group(1)))
        img = requests.get(img_url, headers=headers, timeout=timeout)
        img.raise_for_status()
        if not img.headers.get('Content-Type', 'image/').startswith('image/') or len(img.content) < 2000:
            return None
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(img.content)
        return dest
    except Exception:  # noqa: BLE001  the recipe still shows without a photo
        return None
