"""Radar map around the house: a dark street map (CARTO, cached on disk; it never changes) with the
latest radar on top.

Radar sources, first that answers wins:
1. NOAA MRMS composite base reflectivity (mapservices.weather.noaa.gov), exported for the exact view
2. Iowa State (IEM) NEXRAD composite tiles
"""
import datetime
import io
import math
import os

import requests
from PIL import Image

import config

HEADERS = {'User-Agent': 'frame-daily-plan (github.com/jakehitz-dotcom/frame-daily-plan)'}
CACHE = os.path.join(config.DATA, 'map-tiles')
TILE = 512            # @2x base map tiles
R = 20037508.342789244
MRMS = ('https://mapservices.weather.noaa.gov/eventdriven/rest/services/radar/radar_base_reflectivity/MapServer/'
        'export')
IEM = 'https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0/nexrad-n0q-900913/{z}/{x}/{y}.png'
CARTO = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'


def world_px(lat, lon, zoom):
    n = TILE * 2 ** zoom
    x = (lon + 180) / 360 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def metres_per_px(lat, zoom):
    return 2 * math.pi * 6378137 * math.cos(math.radians(lat)) / (TILE * 2 ** zoom)


def _get(url, timeout=12):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    if not r.headers.get('Content-Type', 'image/').startswith('image/'):
        raise RuntimeError('not an image')
    return Image.open(io.BytesIO(r.content)).convert('RGBA')


def base_map(lat, lon, zoom, w, h):
    """Dark base map centred on (lat, lon), w x h pixels. Tiles are cached forever. None if offline."""
    cx, cy = world_px(lat, lon, zoom)
    x0, y0 = cx - w / 2, cy - h / 2
    img = Image.new('RGBA', (w, h), (10, 14, 22, 255))
    got = 0
    for tx in range(int(x0 // TILE), int((x0 + w) // TILE) + 1):
        for ty in range(int(y0 // TILE), int((y0 + h) // TILE) + 1):
            path = os.path.join(CACHE, f'{zoom}-{tx}-{ty}.png')
            try:
                if os.path.exists(path):
                    tile = Image.open(path).convert('RGBA')
                else:
                    tile = _get(CARTO.format(s='abcd'[(tx + ty) % 4], z=zoom, x=tx, y=ty))
                    os.makedirs(CACHE, exist_ok=True)
                    tile.save(path)
                img.alpha_composite(tile.resize((TILE, TILE)), (round(tx * TILE - x0), round(ty * TILE - y0)))
                got += 1
            except Exception:  # noqa: BLE001  a missing tile leaves dark background
                continue
    return img if got else None


def radar_overlay(lat, lon, zoom, w, h):
    """(image, source) of the latest radar for the same view, or (None, None)."""
    cx, cy = world_px(lat, lon, zoom)
    n = TILE * 2 ** zoom

    def merc(px, py):
        return px / n * 2 * R - R, R - py / n * 2 * R
    xmin, ymax = merc(cx - w / 2, cy - h / 2)
    xmax, ymin = merc(cx + w / 2, cy + h / 2)
    try:
        url = (f'{MRMS}?bbox={xmin},{ymin},{xmax},{ymax}&bboxSR=3857&imageSR=3857&size={w},{h}'
               f'&format=png32&transparent=true&dpi=96&f=image')
        return _get(url, timeout=15).resize((w, h)), 'NOAA MRMS'
    except Exception:  # noqa: BLE001
        pass
    try:  # IEM tiles are 256 px: draw each at 2x to match the @2x base map grid
        img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        x0, y0 = cx - w / 2, cy - h / 2
        for tx in range(int(x0 // TILE), int((x0 + w) // TILE) + 1):
            for ty in range(int(y0 // TILE), int((y0 + h) // TILE) + 1):
                tile = _get(IEM.format(z=zoom, x=tx, y=ty), timeout=10).resize((TILE, TILE), Image.NEAREST)
                img.alpha_composite(tile, (round(tx * TILE - x0), round(ty * TILE - y0)))
        return img, 'IEM NEXRAD'
    except Exception:  # noqa: BLE001
        return None, None


def radar_map(lat, lon, w, h, zoom=9):
    """{'image': RGBA w x h, 'source', 'time', 'm_per_px'}: base map + radar, brightened a little."""
    base = base_map(lat, lon, zoom, w, h)
    if base is None:
        base = Image.new('RGBA', (w, h), (10, 14, 22, 255))
    radar, source = radar_overlay(lat, lon, zoom, w, h)
    if radar is not None:
        a = radar.getchannel('A').point(lambda v: min(255, round(v * 0.92)))
        radar.putalpha(a)
        base.alpha_composite(radar)
    return {'image': base, 'source': source, 'time': datetime.datetime.now().astimezone() if source else None,
            'm_per_px': metres_per_px(lat, zoom), 'has_map': base is not None}
