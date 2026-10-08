"""Seed date demonstrative pentru interfața web Cimitir.

Populează: imagine de fundal a hărții, sectoare, o grilă de locuri de veci cu
coduri (A-01-01 ...), statusuri variate și fotografii atașate la câteva locuri.
Idempotent: rescrie grila de locuri/sectoare la fiecare rulare, dar păstrează
înhumatul/contractul legat de A-01-05.
"""
import os
import sys
import json
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
os.environ.setdefault("CIMITIR_DATA_DIR", os.path.join(_HERE, "data"))

import database as cim  # noqa: E402

BG_URL = "https://images.pexels.com/photos/35525045/pexels-photo-35525045.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940"
PHOTO_URLS = [
    "https://images.pexels.com/photos/28525326/pexels-photo-28525326.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940",
    "https://images.unsplash.com/photo-1652636817388-36ea4aad7177?crop=entropy&cs=srgb&fm=jpg&q=85&w=940",
]


def _download(url: str, dest: str) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            f.write(r.read())
        return os.path.getsize(dest) > 1000
    except Exception as e:
        print("download fail", url, e)
        return False


def main():
    cim.init_db()
    media = cim._media_dir("map")
    bg_path = os.path.join(media, "harta_cimitir.jpg")
    if not os.path.exists(bg_path):
        _download(BG_URL, bg_path)

    W, H = 940, 650
    try:
        from PIL import Image
        with Image.open(bg_path) as im:
            W, H = im.size
    except Exception as e:
        print("PIL fail", e)

    cim.set_setting("map_path", bg_path)
    cim.set_setting("map_bg_w", str(W))
    cim.set_setting("map_bg_h", str(H))
    cim.set_setting("cimitir_nume", "Cimitirul Municipal Central")

    conn = cim.get_connection()
    conn.execute("DELETE FROM locuri_de_veci")
    conn.execute("DELETE FROM sectoare")

    # --- sectoare (contur zonă) ---
    sectors = [
        {"id": "A", "points": [[60, 90], [610, 90], [610, 380], [60, 380]], "color": "#2C3E50"},
        {"id": "B", "points": [[650, 90], [900, 90], [900, 560], [650, 560]], "color": "#2C3E50"},
        {"id": "C", "points": [[60, 410], [610, 410], [610, 560], [60, 560]], "color": "#2C3E50"},
    ]
    for s in sectors:
        conn.execute("INSERT INTO sectoare (id, points, color) VALUES (?, ?, ?)",
                     (s["id"], json.dumps(s["points"]), s["color"]))

    # --- grilă locuri de veci ---
    # status: 0=liber, 1=ocupat, 2=rezervat
    status_map = {
        0: ("Liber", "Liber", 0),
        1: ("Ocupat", "Ocupat", 1),
        2: ("Rezervat", "Rezervat", 0),
    }
    pw, ph = 72, 60
    gx, gy = 16, 30

    def make_plot(sector, parcela, rand, x, y, st):
        cod = f"{sector}-{parcela:02d}-{rand:02d}"
        disp, stare, ocupat = status_map[st]
        pts = [[x, y], [x + pw, y], [x + pw, y + ph], [x, y + ph]]
        conn.execute(
            "INSERT INTO locuri_de_veci (cod_loc, points, color, sector, parcela, rand, numar, "
            "capacitate, disponibilitate, tip, stare, categorie, suprafata, ocupat, observatii, extra_data) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cod, json.dumps(pts), "#0078FF", sector, parcela, rand, rand,
             2, disp, "Simplu", stare, "Individual", 4.5, ocupat, None, "{}"))
        return cod

    # Sector A: parcela 1..3, rand 1..6
    pattern_a = [
        [1, 2, 1, 0, 1, 1],
        [0, 1, 0, 1, 2, 1],
        [1, 0, 1, 1, 0, 2],
    ]
    for pi, parcela in enumerate(range(1, 4)):
        for ri, rand in enumerate(range(1, 7)):
            x = 80 + ri * (pw + gx)
            y = 110 + pi * (ph + gy)
            make_plot("A", parcela, rand, x, y, pattern_a[pi][ri])

    # Sector B (dreapta): parcela 1..5, rand 1..2
    pattern_b = [[1, 0], [2, 1], [0, 1], [1, 2], [1, 0]]
    for pi, parcela in enumerate(range(1, 6)):
        for ri, rand in enumerate(range(1, 3)):
            x = 670 + ri * (pw + gx)
            y = 110 + pi * (ph + gy)
            make_plot("B", parcela, rand, x, y, pattern_b[pi][ri])

    # Sector C (jos): parcela 1, rand 1..6
    pattern_c = [0, 1, 0, 2, 1, 0]
    for ri, rand in enumerate(range(1, 7)):
        x = 80 + ri * (pw + gx)
        y = 430
        make_plot("C", 1, rand, x, y, pattern_c[ri])

    conn.close()

    # --- fotografii pe câteva locuri ---
    targets = ["A-01-05", "A-01-01", "A-02-04", "B-02-01"]
    for i, url in enumerate(PHOTO_URLS):
        tmp = os.path.join("/tmp", f"plot_photo_{i}.jpg")
        if _download(url, tmp):
            for cod in targets:
                # evită dublurile
                existing = cim.list_documents("loc", cod, "poza_loc")
                if len(existing) >= len(PHOTO_URLS):
                    continue
                cim.add_document("loc", cod, "poza_loc", tmp,
                                 caption=f"Fotografie loc {cod}")

    print("Seed complet.")
    conn = cim.get_connection()
    print("locuri:", conn.execute("SELECT COUNT(*) FROM locuri_de_veci").fetchone()[0])
    print("sectoare:", conn.execute("SELECT COUNT(*) FROM sectoare").fetchone()[0])
    print("documente:", conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()
