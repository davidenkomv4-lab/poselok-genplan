"""Генерирует демо-данные посёлка «Сосновый берег» в GeoJSON (WGS 84).

Лес, вода и луг — настоящие, из data/site.json (его готовит prepare_site.py по данным OpenStreetMap).
Участки, цены, статусы и кадастровые номера — выдуманы.
Файлы можно открыть в QGIS, поправить и пересобрать страницу (build.py).
"""
import json, math, random
from pathlib import Path

random.seed(7)
ROOT = Path(__file__).parent
OUT = ROOT / "data"
site = json.loads((OUT / "site.json").read_text(encoding="utf-8"))

# Начало локальной системы координат посёлка (метры) — подобрано prepare_site.py
LAT0, LNG0 = site["origin"]
M_LAT = 111_320.0
M_LNG = 111_320.0 * math.cos(math.radians(LAT0))


def ll(x, y):
    """Метры (x на восток, y на север) -> [lng, lat]."""
    return [round(LNG0 + x / M_LNG, 7), round(LAT0 + y / M_LAT, 7)]


def to_m(c):
    return ((c[0] - LNG0) * M_LNG, (c[1] - LAT0) * M_LAT)


def rect(x1, y1, x2, y2):
    return [[ll(x1, y1), ll(x2, y1), ll(x2, y2), ll(x1, y2), ll(x1, y1)]]


def circle(cx, cy, r, n=28):
    pts = [ll(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return [pts + [pts[0]]]


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy or 1)))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def rings_m(polys):
    return [[to_m(c) for c in ring] for poly in polys for ring in poly]


def rect_to_rings(x1, y1, x2, y2, rings):
    """Расстояние от прямоугольника участка до ближайшей границы полигонов (м)."""
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    best = 1e9
    for ring in rings:
        xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
        if min(xs) - best > x2 or max(xs) + best < x1 or min(ys) - best > y2 or max(ys) + best < y1:
            continue
        for p in ring:  # вершины кольца до сторон прямоугольника
            dx = max(x1 - p[0], 0, p[0] - x2); dy = max(y1 - p[1], 0, p[1] - y2)
            best = min(best, math.hypot(dx, dy))
        for c in corners:
            best = min(best, min(seg_dist(c, ring[i], ring[i + 1]) for i in range(len(ring) - 1)))
    return best


forest_rings = rings_m(site["forest"])
water_rings = rings_m(site["water"])

# Ряды участков: (низ, верх, улица, базовая цена за сотку)
ROWS = [
    (105, 145, "Лесная", 112_000),
    (55, 95, "Лесная", 104_000),
    (5, 45, "Центральная", 98_000),
    (-45, -5, "Центральная", 98_000),
    (-95, -55, "Озёрная", 106_000),
    (-145, -105, "Озёрная", 112_000),
]
GAS_NOW = {"Центральная", "Лесная"}

features = []
num = 1
for y1, y2, street, base in ROWS:
    x = 24
    while x < 316:
        w = random.choice([28, 30, 32, 34, 36, 40])
        if x + w > 320:
            break
        area = round(w * (y2 - y1) / 100, 1)
        t = []
        if rect_to_rings(x, y1, x + w, y2, forest_rings) < 80:
            t.append("forest")
        if rect_to_rings(x, y1, x + w, y2, water_rings) < 170:
            t.append("water")
        if street in GAS_NOW:
            t.append("gas")
        k = 1.0 + (0.14 if "water" in t else 0) + (0.08 if "forest" in t else 0) + random.uniform(-0.04, 0.05)
        price = int(round(area * base * k / 10_000) * 10_000)
        r = random.random()
        status = "free" if r < 0.5 else ("reserved" if r < 0.63 else "sold")
        features.append({
            "type": "Feature",
            "properties": {
                "id": num, "street": street, "area": area, "price": price, "status": status, "tags": t,
                "cadastre": f"50:19:0040312:{1200 + num}", "power_kw": 15,
                "gas": "по границе участка" if "gas" in t else "в 2027 году",
            },
            "geometry": {"type": "Polygon", "coordinates": rect(x, y1, x + w, y2)},
        })
        num += 1
        x += w

plots = {"type": "FeatureCollection", "name": "plots", "features": features}

ctx = []
def add(kind, name, geom, **extra):
    ctx.append({"type": "Feature", "properties": {"kind": kind, "name": name, **extra}, "geometry": geom})

# Настоящие лес, вода и луг (OpenStreetMap)
for poly in site["scrub"]:
    add("meadow", "Луг", {"type": "Polygon", "coordinates": poly})
for poly in site["forest"]:
    add("forest", "Лес", {"type": "Polygon", "coordinates": poly})
for poly in site["water"]:
    add("water", site["water_name"], {"type": "Polygon", "coordinates": poly})
(s, w_), (n_, e) = site["extent"]
add("extent", "Границы карты", {"type": "Polygon", "coordinates": [[[w_, s], [e, s], [e, n_], [w_, n_], [w_, s]]]})

# Общественная территория посёлка
add("park", "Детская площадка и сквер", {"type": "Polygon", "coordinates": rect(330, 5, 390, 95)})
add("park", "Спортплощадка", {"type": "Polygon", "coordinates": rect(330, -95, 390, -5)})

# Улицы
for name, y in [("ул. Лесная", 100), ("ул. Центральная", 0), ("ул. Озёрная", -100)]:
    add("road", name, {"type": "LineString", "coordinates": [ll(10, y), ll(395, y)]})
add("road", "проезд", {"type": "LineString", "coordinates": [ll(10, -100), ll(10, 100)]})
add("road", "проезд", {"type": "LineString", "coordinates": [ll(325, -100), ll(325, 100)]})

# Подъезд от существующей дороги, КПП и магазин у въезда
gx, gy = site["gate"]; rx, ry = site["road_end"]
add("mainroad", "Подъезд", {"type": "LineString", "coordinates": [ll(gx, gy), ll(rx, ry)]})
L = math.hypot(rx - gx, ry - gy) or 1
ux, uy = (rx - gx) / L, (ry - gy) / L          # направление «наружу» от посёлка
px_, py_ = -uy, ux                              # поперёк дороги
add("poi", "КПП и въезд", {"type": "Point", "coordinates": ll(gx + ux * 10, gy + uy * 10)})
add("poi", "Магазин и кафе", {"type": "Point", "coordinates": ll(gx + ux * 40 + px_ * 22, gy + uy * 40 + py_ * 22)})

# Пляж и причал на ближайшем берегу, тропинка к ним от ближайшего конца улицы
sx, sy = site["shore"]; ex, ey = site["pier_end"]
add("beach", "Пляж", {"type": "Polygon", "coordinates": circle(sx, sy, 26)})
vx, vy = ex - sx, ey - sy; vl = math.hypot(vx, vy) or 1
add("pier", "Причал", {"type": "LineString", "coordinates": [ll(sx - vx / vl * 4, sy - vy / vl * 4), ll(ex, ey)]})
ends = [(10, 100), (395, 100), (10, 0), (395, 0), (10, -100), (395, -100), (200, 100), (200, -100)]
fx, fy = min(ends, key=lambda p: math.hypot(p[0] - sx, p[1] - sy))
add("path", "Тропа к пляжу", {"type": "LineString", "coordinates": [ll(fx, fy), ll(sx - vx / vl * 10, sy - vy / vl * 10)]})

# Подписи крупных объектов
add("label", site["water_name"], {"type": "Point", "coordinates": ll(*site["water_label"])}, role="water")
add("label", "Лес", {"type": "Point", "coordinates": ll(*site["forest_label"])}, role="forest")
add("label", "Пляж", {"type": "Point", "coordinates": ll(sx, sy)}, role="beach")

# Деревья посёлка: аллея вдоль Центральной и сквер (настоящий лес рисуется полигонами)
trees = []
for x in range(20, 320, 18):
    trees.append(((x + random.uniform(-2, 2), 3.2), 2.6))
for _ in range(26):
    trees.append(((random.uniform(334, 386), random.uniform(10, 90)), random.uniform(2.5, 4.5)))
for (x, y), r in trees:
    ctx.append({"type": "Feature", "properties": {"kind": "tree", "r": round(r, 1)},
                "geometry": {"type": "Point", "coordinates": ll(x, y)}})

context = {"type": "FeatureCollection", "name": "context", "attribution": site["attribution"], "features": ctx}

(OUT / "plots.geojson").write_text(json.dumps(plots, ensure_ascii=False, indent=1), encoding="utf-8")
(OUT / "context.geojson").write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
counts = {s: sum(f["properties"]["status"] == s for f in features) for s in ("free", "reserved", "sold")}
tags = {t: sum(t in f["properties"]["tags"] for f in features) for t in ("forest", "water")}
print(len(features), "участков", counts, "теги", tags)
