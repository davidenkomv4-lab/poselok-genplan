"""Собирает index.html: подставляет стили Leaflet и данные из data/*.geojson в src.html.

Порядок работы: поправили участки (в QGIS или gen_data.py) -> python build.py -> открыли index.html.
"""
import json
from pathlib import Path

root = Path(__file__).parent
src = (root / "src.html").read_text(encoding="utf-8")
css = (root / "vendor-leaflet.css").read_text(encoding="utf-8")
compact = lambda p: json.dumps(json.loads((root / p).read_text(encoding="utf-8")), ensure_ascii=False, separators=(",", ":"))

html = (src.replace("/*LEAFLET_CSS*/", css)
           .replace("/*PLOTS*/", compact("data/plots.geojson").replace("</", "<\\/"))
           .replace("/*CONTEXT*/", compact("data/context.geojson").replace("</", "<\\/")))
# artifact.html — для публикации ссылкой в Claude (обёртку <head> добавит хостинг)
(root / "artifact.html").write_text(html, encoding="utf-8")
# index.html — самостоятельная страница для своего хостинга / GitHub Pages
head = ('<!doctype html>\n<html lang="ru">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<style>body{margin:0}[hidden]{display:none!important}</style>\n')
standalone = head + html.replace("<div class=\"app\">", "</head>\n<body>\n<div class=\"app\">", 1) + "\n</body>\n</html>\n"
(root / "index.html").write_text(standalone, encoding="utf-8")
print("index.html:", len(standalone.encode("utf-8")) // 1024, "KB")
