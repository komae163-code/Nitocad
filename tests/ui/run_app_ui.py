"""Browser tests for the import workspace shell, the mesh editor and the CAD editor (with a mocked /convert)."""
import json, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from run_core_ui import serve, launch, ROOT

def cube_stl(s=10.0):
    v = [(0,0,0),(s,0,0),(s,s,0),(0,s,0),(0,0,s),(s,0,s),(s,s,s),(0,s,s)]
    f = [(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),(1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    out = bytearray(80) + struct.pack('<I', len(f))
    for a,b,c in f: out += struct.pack('<12fH', 0,0,0, *v[a], *v[b], *v[c], 0)
    return bytes(out)

def run():
    from playwright.sync_api import sync_playwright
    srv = serve(); base = f"http://127.0.0.1:{srv.server_address[1]}"; res = {}
    tmp = Path('/tmp/ui_files'); tmp.mkdir(exist_ok=True); (tmp / 'cube.stl').write_bytes(cube_stl())
    (tmp / 'part.step').write_bytes(b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
    (tmp / 'fake.step').write_bytes(b"this is not a step file")
    (tmp / 'bad.exe').write_bytes(b"MZ")
    with sync_playwright() as p:
        b = launch(p); pg = b.new_page(viewport={'width': 1200, 'height': 900}); errors = []; posts = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        def handle(route, request):
            if request.method == "OPTIONS": return route.fulfill(status=204, headers={"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*"})
            body = request.post_data_buffer or b""
            posts.append({"url": request.url, "has_ops": b'name="ops"' in body, "body": body.decode("latin1", "ignore")})
            if request.url.endswith("/convert"):
                return route.fulfill(status=200, body=cube_stl(25.0), headers={"content-type": "model/stl", "access-control-allow-origin": "*", "access-control-expose-headers": "X-NitoCAD-Stats", "x-nitocad-stats": json.dumps({"size": [25, 25, 25], "solids": 1, "faces": 6, "volume": 15625.0, "valid": True})})
            return route.fulfill(status=200, body=b"ISO-10303-21; fake", headers={"content-type": "application/step", "access-control-allow-origin": "*"})
        pg.route("**/convert", handle); pg.route("**/api/files/cad/export", handle)
        pg.goto(base + "/frontend/import.html")
        res["accept_attr"] = pg.evaluate("document.getElementById('picker').accept")
        # ---- STL: opens, edits, exports
        pg.set_input_files("#picker", str(tmp / "cube.stl")); pg.wait_for_selector(".panel", timeout=8000)
        res["stl_info"] = pg.inner_text(".info")
        pg.fill("input[type=number] >> nth=0", "5"); pg.click("button:text-is('Move')"); pg.wait_for_timeout(200)
        res["after_move_dirty_tab"] = pg.inner_text(".tab span")
        with pg.expect_download() as d: pg.click("button:text-is('STL')")
        out = Path(d.value.path()).read_bytes(); n = struct.unpack('<I', out[80:84])[0]
        res["export_tris"] = n; res["export_first_x"] = struct.unpack('<f', out[84+12:84+16])[0]
        # ---- STEP via mocked server
        pg.set_input_files("#picker", str(tmp / "part.step")); pg.wait_for_function("document.querySelectorAll('.tab').length===2", timeout=8000); pg.wait_for_timeout(600)
        res["cad_info"] = [t for t in pg.locator(".info").all_inner_texts() if "solid" in t]
        pg.fill(".ed-host:visible input[type=number] >> nth=5", "1.5"); pg.click(".ed-host:visible button:text-is('Fillet all')"); pg.wait_for_timeout(500)
        res["cad_posts"] = len(posts); last = posts[-1]["body"]; res["cad_last_ops"] = last[last.find('name="ops"'):].split("\r\n")[2]
        pg.click(".ed-host:visible button:text-is('STEP')"); pg.wait_for_timeout(400)
        res["export_post"] = posts[-1]["url"].split("/api")[-1]; res["export_format"] = "name=\"format\"" in posts[-1]["body"] and "step" in posts[-1]["body"]
        # ---- rejections
        pg.set_input_files("#picker", [str(tmp / "fake.step"), str(tmp / "bad.exe")]); pg.wait_for_timeout(500)
        res["toasts"] = pg.locator(".toast").all_inner_texts(); res["tabs_after_bad"] = pg.locator(".tab").count()
        res["page_errors"] = errors; b.close()
    print(json.dumps(res, indent=1))
if __name__ == "__main__": run()
