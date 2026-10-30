"""Recipes to show full-screen on the Frame: Paprika's "Recipe Text", or a recipe web page's link
(Allrecipes, NYT Cooking, most blogs) read from its schema.org data.

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
import json
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


_NUM = r'[\d½⅓⅔¼¾⅛⅜⅝⅞]+(?:[/.][\d]+)?(?:\s+[\d]+/[\d]+|\s*[½⅓⅔¼¾⅛⅜⅝⅞])?'
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


# ── Recipes from web pages (schema.org Recipe, which almost every recipe site publishes) ────────
BROWSER = {'User-Agent': 'Mozilla/5.0 (Linux; Android 14; Pixel) AppleWebKit/537.36 (KHTML, like Gecko) '
                         'Chrome/126.0 Mobile Safari/537.36',
           'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'en-US,en;q=0.9'}
URL_RE = re.compile(r'https?://[^\s<>"\']+')


def first_url(*texts):
    for t in texts:
        m = URL_RE.search(t or '')
        if m:
            return m.group(0).rstrip(').,')
    return None


def _clean(text):
    text = html.unescape(re.sub(r'<[^>]+>', ' ', str(text or '')))
    return re.sub(r'\s+', ' ', text).strip()


def _duration(iso):
    """'PT1H15M' -> '1 hr 15 min' (None for missing or zero)."""
    m = re.fullmatch(r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?', (iso or '').strip())
    if not m or not any(m.groups()):
        return None
    d, h, mins = (int(g or 0) for g in m.groups())
    h += d * 24
    parts = ([f'{h} hr'] if h else []) + ([f'{mins} min'] if mins else [])
    return ' '.join(parts) or None


def _find_recipe(node):
    """The first object typed Recipe anywhere in a JSON-LD tree (@graph, lists, nested)."""
    if isinstance(node, list):
        for n in node:
            found = _find_recipe(n)
            if found:
                return found
    elif isinstance(node, dict):
        t = node.get('@type')
        if t == 'Recipe' or (isinstance(t, list) and 'Recipe' in t):
            return node
        for key in ('@graph', 'mainEntity', 'itemListElement'):
            if key in node:
                found = _find_recipe(node[key])
                if found:
                    return found
    return None


def _steps(instr):
    """recipeInstructions: a string, a list of strings, HowToSteps, or HowToSections of HowToSteps."""
    if isinstance(instr, str):
        parts = [p for p in re.split(r'\n+|(?<=\.)\s+(?=\d+\.\s)', _clean(instr) if '<' in instr else instr) if p.strip()]
        return [re.sub(r'^\d+[.)]\s+', '', _clean(p)) for p in parts]
    out = []
    for item in instr or []:
        if isinstance(item, str):
            out.append(_clean(item))
        elif isinstance(item, dict):
            if item.get('@type') == 'HowToSection' or 'itemListElement' in item:
                out += _steps(item.get('itemListElement') or [])
            else:
                out.append(_clean(item.get('text') or item.get('name')))
    return [s for s in out if s]


def _image(img):
    if isinstance(img, list) and img:
        return _image(img[0])
    if isinstance(img, dict):
        return img.get('url') or img.get('contentUrl')
    return img if isinstance(img, str) else None


def from_json_ld(page_html, url=''):
    """A recipe dict (same shape as parse()) from a page's schema.org data, or None."""
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                            page_html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        r = _find_recipe(data)
        if not r:
            continue
        yield_ = r.get('recipeYield')
        if isinstance(yield_, list):
            yield_ = next((y for y in yield_ if re.search(r'\d', str(y))), yield_[0] if yield_ else '')
        servings = re.sub(r'(?i)\s*servings?$', '', _clean(yield_)) if yield_ else ''
        meta = {k: v for k, v in (('prep', _duration(r.get('prepTime'))), ('cook', _duration(r.get('cookTime'))),
                                  ('total', _duration(r.get('totalTime'))), ('servings', servings)) if v}
        ingredients = [_clean(i) for i in r.get('recipeIngredient') or r.get('ingredients') or [] if _clean(i)]
        steps = _steps(r.get('recipeInstructions'))
        if not ingredients or not steps:
            continue
        return {'name': _clean(r.get('name')) or 'Recipe', 'meta': meta, 'ingredients': ingredients,
                'steps': steps, 'notes': '', 'source': url, 'image': urllib.parse.urljoin(url, _image(r.get('image')) or '')
                or None}
    return None


def from_url(url, timeout=12):
    """Fetch a recipe page and read its recipe. Raises with a plain reason when it can't."""
    r = requests.get(url, headers=BROWSER, timeout=timeout)
    if r.status_code in (401, 403, 429):
        raise RuntimeError(f'{urllib.parse.urlparse(url).netloc} refused the request ({r.status_code})')
    r.raise_for_status()
    rec = from_json_ld(r.text, r.url)
    if not rec:
        raise RuntimeError('no recipe found on that page')
    return rec


def fetch_image(img_url, dest, timeout=8):
    try:
        img = requests.get(img_url, headers=BROWSER, timeout=timeout)
        img.raise_for_status()
        if not img.headers.get('Content-Type', 'image/').startswith('image/') or len(img.content) < 2000:
            return None
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as f:
            f.write(img.content)
        return dest
    except Exception:  # noqa: BLE001
        return None
