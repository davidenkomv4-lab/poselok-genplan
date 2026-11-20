"""Подготовка площадки: реальные лес и вода вокруг посёлка из тайлов OpenStreetMap.

Это маленький ГИС-конвейер:
  1. скачиваем тайлы OSM вокруг выбранного места (Озернинское водохранилище, Рузский округ);
  2. классифицируем растр по цвету: вода, лес, луг, открытое поле, всё остальное;
  3. чистим маски морфологией (закрытие/открытие), чтобы убрать значки деревьев и подписи;
  4. векторизуем маски в полигоны (трассировка границ + упрощение Дугласа–Пекера + сглаживание);
  5. подбираем, куда поставить посёлок: целиком в поле, с отступом от леса и воды, поближе к воде;
  6. сохраняем всё в data/site.json — его читает gen_data.py.

Данные карты: © участники OpenStreetMap (ODbL). Тайлы кешируются в .tiles/.
"""
import json, math, sys, time, urllib.request
from pathlib import Path
sys.setrecursionlimit(100_000)

import numpy as np
from PIL import Image

ROOT = Path(__file__).parent
CENTER = (55.7785, 36.2960)   # поле на полуострове, восточный берег водохранилища
Z, NX, NY = 16, 8, 8          # 8×8 тайлов ≈ 2,7 × 2,7 км
CELL = 3                      # пикселей в ячейке векторизации (~4 м)

# ---------- 1. тайлы ----------
cache = ROOT / ".tiles"; cache.mkdir(exist_ok=True)
n = 2 ** Z
fx = (CENTER[1] + 180) / 360 * n
fy = (1 - math.asinh(math.tan(math.radians(CENTER[0]))) / math.pi) / 2 * n
x0, y0 = int(fx) - NX // 2, int(fy) - NY // 2
mosaic = Image.new("RGB", (256 * NX, 256 * NY))
for j in range(NY):
    for i in range(NX):
        p = cache / f"{Z}_{x0 + i}_{y0 + j}.png"
        if not p.exists():
            req = urllib.request.Request(f"https://tile.openstreetmap.org/{Z}/{x0 + i}/{y0 + j}.png",
                                         headers={"User-Agent": "gis-demo-site-prep/1.0"})
            p.write_bytes(urllib.request.urlopen(req, timeout=30).read()); time.sleep(0.2)
        mosaic.paste(Image.open(p).convert("RGB"), (256 * i, 256 * j))
a = np.asarray(mosaic).astype(int)
H, W = a.shape[:2]

def px2ll(px, py):
    tx, ty = x0 + px / 256, y0 + py / 256
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n)))), tx / n * 360 - 180

RES = 156543.03392 * math.cos(math.radians(CENTER[0])) / n   # метров в пикселе

# ---------- 2. классификация по цвету стиля OSM Carto ----------
r, g, b = a[..., 0], a[..., 1], a[..., 2]
near = lambda rgb, tol: (np.abs(a - np.array(rgb)).sum(axis=2) <= tol)
water = (b > r + 35) & (b > 195)
forest = (g > r + 20) & (g > b + 25) & (r < 185)          # фон леса и значки деревьев
scrub = near((200, 215, 171), 10)                          # луг / кустарник
open_land = near((238, 240, 213), 8) | near((242, 239, 233), 6) | scrub   # пашня, пустырь, луг

# ---------- 3. морфология ----------
def shift_or(m, k):
    out = m.copy()
    for dy in range(-k, k + 1):
        for dx in range(-k, k + 1):
            out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out
dilate = shift_or
erode = lambda m, k: ~shift_or(~m, k)
close = lambda m, k: erode(dilate(m, k), k)
opening = lambda m, k: dilate(erode(m, k), k)

forest = opening(close(forest, 2), 2)
water = opening(close(water, 1), 1)
scrub = opening(close(scrub, 2), 3)
open_land = close(open_land, 2)     # тонкие тропинки и подписи не должны «резать» поле

# ---------- 4. векторизация ----------
def to_cells(m):
    h, w = (H // CELL) * CELL, (W // CELL) * CELL
    return m[:h, :w].reshape(h // CELL, CELL, w // CELL, CELL).mean(axis=(1, 3)) > 0.5

def trace(mask):
    """Границы маски как замкнутые кольца по рёбрам ячеек (обход по часовой в координатах экрана)."""
    m = np.pad(mask, 1)
    edges = {}
    ys, xs = np.nonzero(m)
    for y, x in zip(ys, xs):
        if not m[y - 1, x]: edges.setdefault((x, y), []).append((x + 1, y))
        if not m[y, x + 1]: edges.setdefault((x + 1, y), []).append((x + 1, y + 1))
        if not m[y + 1, x]: edges.setdefault((x + 1, y + 1), []).append((x, y + 1))
        if not m[y, x - 1]: edges.setdefault((x, y + 1), []).append((x, y))
    rings = []
    while edges:
        start = next(iter(edges)); ring = [start]; cur = start
        while True:
            nxt = edges[cur].pop()
            if not edges[cur]: del edges[cur]
            if nxt == start: break
            ring.append(nxt); cur = nxt
        rings.append([((x - 1) * CELL, (y - 1) * CELL) for x, y in ring])
    return rings

def dp(pts, tol):
    if len(pts) < 3: return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1; L = math.hypot(dx, dy) or 1
    d = [abs(dy * (x - x1) - dx * (y - y1)) / L for x, y in pts[1:-1]]
    i = max(range(len(d)), key=d.__getitem__)
    if d[i] <= tol: return [pts[0], pts[-1]]
    return dp(pts[: i + 2], tol)[:-1] + dp(pts[i + 1:], tol)

def simplify_ring(ring, tol):
    k = len(ring) // 2
    return dp(ring[: k + 1], tol)[:-1] + dp(ring[k:] + [ring[0]], tol)[:-1]

def chaikin(ring, it=2):
    for _ in range(it):
        out = []
        for i, p in enumerate(ring):
            q = ring[(i + 1) % len(ring)]
            out += [(.75 * p[0] + .25 * q[0], .75 * p[1] + .25 * q[1]), (.25 * p[0] + .75 * q[0], .25 * p[1] + .75 * q[1])]
        ring = out
    return ring

def signed_area(ring):
    return sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1] for i in range(len(ring))) / 2

def inside(pt, ring):
    x, y = pt; ins = False
    for i in range(len(ring)):
        (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % len(ring)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: ins = not ins
    return ins

def polygons(mask, min_m2):
    rings = []
    for ring in trace(to_cells(mask)):
        s = simplify_ring(ring, CELL * 0.9)
        if len(s) < 4: continue
        area = signed_area(s) * RES * RES
        if abs(area) < min_m2: continue
        rings.append((area, chaikin(s)))
    outers = [r for a_, r in rings if a_ > 0]
    holes = [r for a_, r in rings if a_ < 0]
    polys = [[o] for o in outers]
    for h in holes:
        for p in sorted(polys, key=lambda p: abs(signed_area(p[0]))):
            if inside(h[0], p[0]): p.append(h); break
    def geo(ring):
        pts = [[round(px2ll(x, y)[1], 7), round(px2ll(x, y)[0], 7)] for x, y in ring]
        return pts + [pts[0]]
    return [[geo(r) for r in p] for p in polys]

# ---------- 5. где поставить посёлок ----------
# Габарит посёлка в его локальных метрах (x на восток, y на север): улицы, участки, сквер.
FX0, FX1, FY0, FY1 = -2, 400, -152, 152
margin_px = int(10 / RES)
ok = erode(open_land & ~forest & ~water, margin_px)
# для пляжа и «близости к воде» берём только крупную воду: пруды уже 60 м отбрасываются открытием
big_water = opening(water, int(30 / RES))
water_pts = np.argwhere(big_water[::4, ::4]) * 4
forest_pts = np.argwhere(forest[::4, ::4]) * 4

def rect_px(ox, oy):  # ox, oy — пиксель, где у посёлка локальный (0, 0)
    return ox + FX0 / RES, oy - FY1 / RES, ox + FX1 / RES, oy - FY0 / RES

def rect_dist(pts, x1, y1, x2, y2):
    dx = np.maximum(np.maximum(x1 - pts[:, 1], 0), pts[:, 1] - x2)
    dy = np.maximum(np.maximum(y1 - pts[:, 0], 0), pts[:, 0] - y2)
    return float(np.sqrt(dx * dx + dy * dy).min()) * RES

best = None
cx_px, cy_px = (fx - x0) * 256, (fy - y0) * 256     # CENTER в пикселях мозаики
search_r = 550 / RES                               # ищем в радиусе 550 м от выбранного поля
for oy in range(0, H, 6):
    for ox in range(0, W, 6):
        if math.hypot(ox + 200 / RES - cx_px, oy - cy_px) > search_r: continue
        x1, y1, x2, y2 = map(int, rect_px(ox, oy))
        if x1 < 0 or y1 < 0 or x2 >= W or y2 >= H: continue
        if not ok[y1:y2:4, x1:x2:4].all(): continue
        score = rect_dist(water_pts, x1, y1, x2, y2) + 0.5 * rect_dist(forest_pts, x1, y1, x2, y2)
        if best is None or score < best[0]: best = (score, ox, oy)
assert best, "посёлок не помещается в поле — уменьшите габарит или сдвиньте CENTER"
_, OX, OY = best
LAT0, LNG0 = px2ll(OX, OY)
to_px = lambda x, y: (OX + x / RES, OY - y / RES)
to_m = lambda px, py: ((px - OX) * RES, (OY - py) * RES)
print(f"посёлок: origin {LAT0:.6f}, {LNG0:.6f}; до воды/леса {best[0]:.0f} (балл)")

# въезд: конец улицы, ближайший к существующей дороге (белые линии на карте) и не через воду
road_pts = np.argwhere(near((255, 255, 255), 0))[::3]
gate_opts = {"W": (10, 0), "E": (395, 0), "NW": (10, 100), "NE": (395, 100), "SW": (10, -100), "SE": (395, -100)}
def path_ok(p, q):
    for t in np.linspace(0, 1, 60):
        x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
        if water[int(y), int(x)]: return False
    return True
cands = []
for name, (gx, gy) in gate_opts.items():
    px, py = to_px(gx, gy)
    d = np.hypot(road_pts[:, 1] - px, road_pts[:, 0] - py)
    for k in np.argsort(d)[:40]:
        q = (road_pts[k][1], road_pts[k][0])
        if path_ok((px, py), q):
            cands.append((d[k] * RES, name, (gx, gy), to_m(*q))); break
gate_len, gate_name, gate, road_end = min(cands)
print(f"въезд: {gate_name}, подъезд {gate_len:.0f} м")

# пляж: ближайшая к посёлку точка берега, причал уходит в воду
cxp, cyp = to_px(200, 0)
x1, y1, x2, y2 = rect_px(OX, OY)
dx = np.maximum(np.maximum(x1 - water_pts[:, 1], 0), water_pts[:, 1] - x2)
dy = np.maximum(np.maximum(y1 - water_pts[:, 0], 0), water_pts[:, 0] - y2)
k = int(np.argmin(dx * dx + dy * dy))
shore_px = (float(water_pts[k][1]), float(water_pts[k][0]))
shore = to_m(*shore_px)
vx, vy = shore[0] - 200, shore[1]; L = math.hypot(vx, vy); vx, vy = vx / L, vy / L
pier_end = (shore[0] + vx * 32, shore[1] + vy * 32)

# подписи: в воде — 150 м от пляжа дальше по направлению причала; в лесу — ближайшая лесная точка к северу от посёлка
water_label = (shore[0] + vx * 150, shore[1] + vy * 150)
fy_px = int(to_px(200, 230)[1]); fx_px = int(to_px(200, 230)[0])
fp = forest_pts[np.argmin(np.hypot(forest_pts[:, 1] - fx_px, forest_pts[:, 0] - fy_px))]
forest_label = to_m(fp[1], fp[0])

nw, se = px2ll(0, 0), px2ll(W, H)
site = {
    "attribution": "© участники OpenStreetMap",
    "origin": [round(LAT0, 7), round(LNG0, 7)],
    "extent": [[se[0], nw[1]], [nw[0], se[1]]],
    "water": polygons(water, 400),
    "forest": polygons(forest, 600),
    "scrub": polygons(scrub, 800),
    "gate": gate, "gate_side": gate_name, "road_end": [round(v, 1) for v in road_end],
    "shore": [round(v, 1) for v in shore], "pier_end": [round(v, 1) for v in pier_end],
    "water_label": [round(v, 1) for v in water_label], "forest_label": [round(v, 1) for v in forest_label],
    "water_name": "Озернинское водохранилище",
}
(ROOT / "data").mkdir(exist_ok=True)
(ROOT / "data" / "site.json").write_text(json.dumps(site, ensure_ascii=False), encoding="utf-8")
print("полигонов: вода", len(site["water"]), "лес", len(site["forest"]), "луг", len(site["scrub"]))
