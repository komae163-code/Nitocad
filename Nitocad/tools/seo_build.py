#!/usr/bin/env python3
"""
Builds and stamps the SEO files for the NitoCAD frontend. Safe to run again.

  python tools/seo_build.py

Set SITE_URL below (or the SITE_URL environment variable) to the production origin, with no
trailing slash. Every canonical link, social tag, sitemap entry and structured-data URL uses it.
What it writes:
  frontend/robots.txt, frontend/sitemap.xml, frontend/og-image.png
  frontend/text-to-cad.html, b-rep-vs-mesh.html, how-verification-works.html, text-to-step.html
  and, in the existing pages, a marked block of head tags (canonical, Open Graph, Twitter, robots).
"""
from __future__ import annotations

import html
import os
import re
from datetime import date
from pathlib import Path

SITE_URL = os.environ.get("SITE_URL", "https://nitocad.vercel.app").rstrip("/")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
TODAY = date.today().isoformat()
BEGIN, END = "<!-- seo:begin -->", "<!-- seo:end -->"
OG_IMAGE = f"{SITE_URL}/og-image.png"

# Pages people can find through search.
INDEXED = {
    "index.html": "/",
    "pricing.html": "/pricing.html",
    "exports.html": "/exports.html",
    "whitepaper.html": "/whitepaper.html",
    "studio.html": "/studio.html",
    "civil.html": "/civil.html",
    "text-to-cad.html": "/text-to-cad.html",
    "b-rep-vs-mesh.html": "/b-rep-vs-mesh.html",
    "how-verification-works.html": "/how-verification-works.html",
    "text-to-step.html": "/text-to-step.html",
    "terms.html": "/terms.html",
    "privacy.html": "/privacy.html",
}
# Account and working pages. They stay crawlable so the noindex tag can be seen.
NOINDEX = ["profile.html", "refine.html", "preview.html", "import.html", "aerospace.html", "exploded.html"]

HOME_TITLE = "NitoCAD | AI Text-to-CAD, STEP Files and Engineering Verification"
HOME_DESC = ("Turn plain-English descriptions into 3D CAD models. NitoCAD builds B-rep solids, checks every "
             "version against your requirements with PASS, FAIL or UNKNOWN, and exports STEP, IGES, DXF, PDF and STL.")

NAV = ('<a href="index.html">Home</a><a href="pricing.html">Pricing</a><a href="exports.html">Exports</a>'
       '<a href="whitepaper.html">White paper</a><a href="studio.html" class="btn btn-primary">Open CAD Studio</a>')


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def head_tags(path: str, title: str, desc: str, *, noindex: bool = False) -> str:
    url = SITE_URL + path
    robots = '<meta name="robots" content="noindex, follow">\n' if noindex else ""
    if noindex:
        return f"{BEGIN}\n{robots}{END}"
    return (
        f"{BEGIN}\n"
        f'<link rel="canonical" href="{url}">\n'
        f'<meta property="og:type" content="website">\n'
        f'<meta property="og:site_name" content="NitoCAD">\n'
        f'<meta property="og:title" content="{esc(title)}">\n'
        f'<meta property="og:description" content="{esc(desc)}">\n'
        f'<meta property="og:url" content="{url}">\n'
        f'<meta property="og:image" content="{OG_IMAGE}">\n'
        f'<meta property="og:image:width" content="1200">\n'
        f'<meta property="og:image:height" content="630">\n'
        f'<meta name="twitter:card" content="summary_large_image">\n'
        f'<meta name="twitter:title" content="{esc(title)}">\n'
        f'<meta name="twitter:description" content="{esc(desc)}">\n'
        f'<meta name="twitter:image" content="{OG_IMAGE}">\n'
        f"{END}"
    )


def stamp(page: str, block: str) -> None:
    p = FRONTEND / page
    s = p.read_text()
    s = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", s, flags=re.S)
    s = s.replace("</head>", block + "\n</head>", 1)
    p.write_text(s)


FALLBACK_DESC = {
    "terms.html": "The terms that apply when you use NitoCAD, including accounts, plans, payments and acceptable use.",
    "privacy.html": "How NitoCAD collects, uses and protects your data.",
}


def read_meta(page: str) -> tuple[str, str]:
    p = FRONTEND / page
    s = p.read_text()
    t = re.search(r"<title>(.*?)</title>", s, re.S)
    d = re.search(r'<meta name="description" content="(.*?)">', s, re.S)
    if d is None:
        desc = FALLBACK_DESC.get(page, HOME_DESC)
        s = s.replace("</title>", f'</title>\n<meta name="description" content="{esc(desc)}">', 1)
        p.write_text(s)
        return html.unescape(t.group(1).strip()) if t else "NitoCAD", desc
    return html.unescape(t.group(1).strip()) if t else "NitoCAD", html.unescape(d.group(1))


# ------------------------------------------------------------------ content pages

PAGE_CSS = """
  :root{--ink:#1C1C1E;--ink-soft:#5E5E63;--orange:#1C1C1E;--dot:#E8601C;--orange-deep:#0E0E10;--paper:#FAFAF8;--fog:#F0F0EE;--line:#E3E3E0;--radius:14px;--ok:#13A57D;--bad:#B3261E;}
  *{box-sizing:border-box;}
  body{margin:0;background:var(--paper);color:var(--ink);font-family:'IBM Plex Sans',sans-serif;-webkit-font-smoothing:antialiased;line-height:1.7;}
  a{color:var(--orange-deep);}
  h1,h2,h3{font-family:'Fraunces',serif;font-weight:700;margin:0;color:var(--ink);line-height:1.2;}
  .wrap{max-width:860px;margin:0 auto;padding:0 28px;}
  :focus-visible{outline:2px solid var(--orange-deep);outline-offset:3px;}
  .sp-head{border-bottom:1px solid var(--line);}
  .sp-head .wrap{display:flex;align-items:center;justify-content:space-between;height:72px;max-width:1080px;}
  .logo{font-family:'Fraunces',serif;font-style:italic;font-weight:500;font-size:1.5rem;text-decoration:none;color:var(--ink);}
  .logo span{color:var(--dot);}
  .sp-nav{display:flex;gap:24px;align-items:center;}
  .sp-nav a{text-decoration:none;color:var(--ink-soft);font-size:.92rem;font-weight:500;}
  .sp-nav a:hover{color:var(--ink);}
  .btn{display:inline-flex;align-items:center;padding:11px 20px;border-radius:999px;font-weight:600;font-size:.92rem;text-decoration:none;border:1.5px solid transparent;}
  .btn-primary{background:var(--orange);color:#fff !important;}
  .btn-ghost{border-color:var(--ink);color:var(--ink);}
  article{padding:56px 0 72px;}
  article h1{font-size:2.4rem;margin-bottom:18px;}
  article h2{font-size:1.5rem;margin:44px 0 12px;}
  article h3{font-size:1.1rem;margin:22px 0 6px;}
  article p,article li{color:var(--ink-soft);font-size:1.02rem;}
  article p.lead{font-size:1.15rem;color:var(--ink);}
  article ul,article ol{padding-left:22px;}
  .tbl{overflow-x:auto;border:1px solid var(--line);border-radius:var(--radius);margin:18px 0;}
  table{border-collapse:collapse;width:100%;min-width:560px;font-size:.95rem;}
  th,td{padding:12px 16px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;}
  thead th{background:var(--fog);font-family:'Fraunces',serif;}
  tbody tr:last-child td{border-bottom:0;}
  .pass{color:var(--ok);font-weight:700;}.fail{color:var(--bad);font-weight:700;}.unk{color:#8a6d00;font-weight:700;}
  .note{border-left:3px solid var(--dot);padding:4px 0 4px 16px;margin:20px 0;color:var(--ink);}
  .cta{display:flex;gap:12px;flex-wrap:wrap;margin:32px 0 0;}
  .related{margin-top:48px;padding-top:20px;border-top:1px solid var(--line);}
  .related a{display:block;margin:6px 0;}
  footer{border-top:1px solid var(--line);padding:26px 0;color:var(--ink-soft);font-size:.88rem;}
  footer .wrap{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;max-width:1080px;}
  footer a{color:var(--ink-soft);}
  @media (max-width:760px){.sp-nav a:not(.btn){display:none;}article h1{font-size:1.9rem;}}
"""

RELATED = {
    "text-to-cad.html": "AI text-to-CAD",
    "b-rep-vs-mesh.html": "B-rep solids vs mesh models",
    "how-verification-works.html": "How NitoCAD verifies designs",
    "text-to-step.html": "Generate a STEP file from text",
}

CTA = ('<div class="cta"><a class="btn btn-primary" href="index.html#demo">Preview a part free</a>'
       '<a class="btn btn-ghost" href="pricing.html">See plans</a></div>')

PAGES = {
    "text-to-cad.html": dict(
        title="AI Text-to-CAD for Mechanical Parts | NitoCAD",
        desc=("Describe a part in plain English and NitoCAD builds a B-rep solid, checks it against your "
              "requirements and exports STEP, IGES, DXF, PDF and STL. Preview free."),
        h1="AI text-to-CAD that checks its own work",
        body="""
<p class="lead">Text-to-CAD turns a written description of a part into a 3D CAD model. NitoCAD builds an editable B-rep solid from your description, checks each version against the requirements you set, and exports files a shop can use.</p>

<h2>How it works</h2>
<ol>
<li><b>Describe.</b> Write the part in plain English, or attach a STEP file, a DXF or a sketch. Every dimension you type is kept exactly as written. If NitoCAD sees a problem with a dimension, it flags it and leaves the value alone.</li>
<li><b>Build.</b> NitoCAD generates a B-rep solid on OpenCASCADE. You see a free STL preview first, with no wallet required.</li>
<li><b>Check.</b> Your requirements stay attached to the part and are checked again on every version. Each check answers PASS, FAIL or UNKNOWN. <a href="how-verification-works.html">How verification works</a>.</li>
<li><b>Export.</b> Generate STEP, IGES, DXF, PDF, STL, GLB, 3MF or URDF files in CAD Studio.</li>
</ol>

<h2>What you can describe</h2>
<p>Gears, shafts, pistons, brackets, flanges, springs, bolts, enclosures, bearings, pulleys and brake discs, plus whole assemblies such as an inline-4 engine with 26 parts. You can also say what the part must do, for example "fits in 100 x 80 x 60 mm" or "under 200 g", and NitoCAD measures every new version against it.</p>

<h2>What it does not do</h2>
<p>NitoCAD does not certify a design. Checks use typical shop guidelines, not your shop's limits. Aerospace results stop at "ready for engineering review", civil checks are screening level and building rules are conceptual. A licensed engineer must verify anything that gets built.</p>

<h2>Text-to-CAD compared with mesh generators</h2>
<p>Many AI 3D tools produce a mesh, which looks right but is hard to edit or machine from. NitoCAD builds B-rep solids, the representation CAD and CAM software work with. <a href="b-rep-vs-mesh.html">B-rep solids vs mesh models</a> explains the difference.</p>
""" + CTA,
    ),
    "b-rep-vs-mesh.html": dict(
        title="B-rep Solid Models vs Mesh Models: STEP vs STL | NitoCAD",
        desc=("B-rep solids describe exact surfaces and edges. Meshes approximate a surface with triangles. "
              "Learn when to use STEP, IGES or STL, and how NitoCAD produces both."),
        h1="B-rep solid models vs mesh models",
        body="""
<p class="lead">A B-rep (boundary representation) solid describes a part as exact surfaces and edges that enclose a volume. A mesh approximates the surface with triangles. STEP and IGES files normally carry B-rep geometry. STL files carry a triangle mesh.</p>

<div class="tbl"><table>
<thead><tr><th></th><th>B-rep solid</th><th>Mesh</th></tr></thead>
<tbody>
<tr><td>Surface</td><td>Exact surfaces and edges</td><td>Flat triangles that approximate the shape</td></tr>
<tr><td>Typical files</td><td>STEP, IGES</td><td>STL, 3MF, GLB</td></tr>
<tr><td>Editing in CAD</td><td>Faces, edges and holes can be selected and changed</td><td>Hard to edit. Features are lost in the triangles</td></tr>
<tr><td>Machining and drawings</td><td>Suited to CNC programming and dimensioned drawings</td><td>Poorly suited. Curved faces are faceted</td></tr>
<tr><td>3D printing and viewing</td><td>Usually converted to a mesh first</td><td>Used directly by slicers and viewers</td></tr>
</tbody></table></div>

<h2>Which one do you need?</h2>
<ul>
<li>Send STEP when someone will machine the part, edit it, or put it in an assembly.</li>
<li>Send STL or 3MF when the next step is a 3D printer.</li>
<li>Use a mesh when you only need a quick look at the shape.</li>
</ul>

<h2>How NitoCAD uses both</h2>
<p>The free preview is an STL mesh, so you can see the part before you commit. In CAD Studio, NitoCAD builds a true B-rep solid on OpenCASCADE and exports STEP and IGES for CAD and CAM work, DXF and PDF for drawings, and STL, 3MF and GLB when you need a mesh. See <a href="exports.html">all export formats</a>.</p>

<p class="note">Converting a mesh back into an editable solid is a hard problem and rarely gives clean results. Starting from a B-rep solid avoids it.</p>
""" + CTA,
    ),
    "how-verification-works.html": dict(
        title="How NitoCAD Verifies AI-Generated CAD Designs | NitoCAD",
        desc=("NitoCAD checks every version against your requirements and answers PASS, FAIL or UNKNOWN. "
              "UNKNOWN is never shown as a pass. See what is checked and where the checks stop."),
        h1="How NitoCAD verifies AI-generated CAD designs",
        body="""
<p class="lead">NitoCAD does not treat a generated model as correct. It checks each version against your requirements and answers PASS, FAIL or UNKNOWN. UNKNOWN is never shown as a pass.</p>

<div class="tbl"><table>
<thead><tr><th>Result</th><th>Meaning</th></tr></thead>
<tbody>
<tr><td class="pass">PASS</td><td>The check ran and the part meets the stated requirement.</td></tr>
<tr><td class="fail">FAIL</td><td>The check ran and the part does not meet the stated requirement.</td></tr>
<tr><td class="unk">UNKNOWN</td><td>NitoCAD could not measure it, or an input was missing. It is never reported as a pass.</td></tr>
</tbody></table></div>

<h2>An example</h2>
<p>These rows show how a bracket might be reported. They are an illustration, not a saved result.</p>
<div class="tbl"><table>
<thead><tr><th>Requirement</th><th>You said</th><th>Measured</th><th>Result</th></tr></thead>
<tbody>
<tr><td>Envelope</td><td>Fits in 100 x 80 x 60 mm</td><td>96 x 62 x 40 mm</td><td class="pass">PASS</td></tr>
<tr><td>Mass</td><td>Under 200 g</td><td>212 g</td><td class="fail">FAIL</td></tr>
<tr><td>Wall</td><td>At least 2 mm</td><td>3 mm</td><td class="pass">PASS</td></tr>
<tr><td>Material</td><td>6061 aluminium</td><td>not stated</td><td class="unk">UNKNOWN</td></tr>
</tbody></table></div>

<h2>What gets checked</h2>
<ol>
<li><b>The solid is valid.</b> NitoCAD checks the geometry before anything else.</li>
<li><b>Your requirements.</b> They stay attached to the part and are checked again on every version, so an edit that quietly breaks an earlier requirement is caught when it is built, not when the part arrives.</li>
<li><b>Manufacturability.</b> Guideline checks for CNC machining, sheet metal, molding and 3D printing.</li>
<li><b>Assemblies.</b> Overlap and mass checks across the parts.</li>
</ol>

<h2>Where the checks stop</h2>
<p class="note">Geometric validity does not prove a part is safe, manufacturable or fit for its purpose.</p>
<p>Checks use typical shop guidelines, not your shop's limits. Aerospace results stop at "ready for engineering review". Civil checks are screening level and building rules are conceptual. A licensed engineer must verify anything that gets built. NitoCAD also keeps your numbers: if it sees a problem with a dimension, it flags it and leaves the value alone.</p>

<p>If a generation fails, you are not charged for it.</p>
""" + CTA,
    ),
    "text-to-step.html": dict(
        title="Generate a STEP File From a Text Description | NitoCAD",
        desc=("Describe a part, preview it free, then generate a STEP file in NitoCAD CAD Studio. "
              "STEP carries exact B-rep geometry that opens in other CAD tools."),
        h1="Generate a STEP file from a text description",
        body="""
<p class="lead">STEP is the standard file format for exchanging exact 3D geometry between CAD and CAM systems. With NitoCAD you describe a part in plain English, check a free preview, then generate the STEP file in CAD Studio.</p>

<h2>Steps</h2>
<ol>
<li><b>Describe the part.</b> Give the shape, the dimensions with units and what it must do. For example: "L-bracket, 100 mm long, 60 mm wide, 40 mm high, 3 mm wall, two 8 mm holes, under 200 g." This is an example prompt.</li>
<li><b>Look at the free preview.</b> The preview is an STL mesh and needs no wallet. Change the description until the shape is right.</li>
<li><b>Sign in to CAD Studio.</b> Keep refining in the same chat, then generate the files.</li>
<li><b>Download the STEP file.</b> IGES, DXF, PDF, STL, GLB, 3MF and URDF are available too.</li>
<li><b>Open it in your CAD or CAM tool</b> and confirm the dimensions before you manufacture.</li>
</ol>

<h2>What is inside the file</h2>
<p>NitoCAD builds a B-rep solid on OpenCASCADE, so the STEP file holds exact surfaces and edges rather than triangles. Other CAD tools can select faces, measure edges and use the part in an assembly. <a href="b-rep-vs-mesh.html">Why that matters</a>.</p>

<h2>Tips for better results</h2>
<ul>
<li>State units and every dimension that matters. NitoCAD keeps your numbers exactly as written.</li>
<li>Add requirements such as envelope, mass, wall thickness or material, so each version is checked against them. <a href="how-verification-works.html">How verification works</a>.</li>
<li>Attach a STEP file, DXF or sketch when you are starting from something that exists.</li>
</ul>

<p class="note">The file is a starting point for engineering work, not a certified design. Check dimensions before you manufacture.</p>
""" + CTA,
    ),
}


def build_page(name: str, spec: dict) -> None:
    related = "".join(
        f'<a href="{p}">{esc(t)}</a>' for p, t in RELATED.items() if p != name
    )
    doc = f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(spec['title'])}</title>
<meta name="description" content="{esc(spec['desc'])}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,700;1,9..144,500&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{PAGE_CSS}</style>
<link rel="stylesheet" href="theme.css">
<script src="theme.js"></script>
</head>
<body>
<header class="sp-head"><div class="wrap">
<a href="index.html" class="logo">Nit<span>o</span>CAD</a>
<nav class="sp-nav" aria-label="Site">{NAV}</nav>
</div></header>
<main><article><div class="wrap">
<h1>{esc(spec['h1'])}</h1>
{spec['body']}
<div class="related"><b>Related</b>{related}</div>
</div></article></main>
<footer><div class="wrap"><span>&copy; 2026 NitoCAD</span><span><a href="terms.html">Terms of Service</a> &nbsp; <a href="privacy.html">Privacy Policy</a></span></div></footer>
</body>
</html>
"""
    (FRONTEND / name).write_text(doc)


# ------------------------------------------------------------------ structured data

def home_jsonld() -> str:
    import json
    data = [
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": "NitoCAD",
            "url": SITE_URL + "/",
            "logo": SITE_URL + "/logo-mark.png",
        },
        {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            "name": "NitoCAD",
            "url": SITE_URL + "/",
            "applicationCategory": "DesignApplication",
            "operatingSystem": "Web",
            "description": HOME_DESC,
            "offers": [
                {"@type": "Offer", "name": "Free", "price": "0", "priceCurrency": "USD"},
                {"@type": "Offer", "name": "Pro", "price": "20", "priceCurrency": "USD",
                 "priceSpecification": {"@type": "UnitPriceSpecification", "price": "20", "priceCurrency": "USD", "billingDuration": "P1M"}},
                {"@type": "Offer", "name": "Max", "price": "80", "priceCurrency": "USD",
                 "priceSpecification": {"@type": "UnitPriceSpecification", "price": "80", "priceCurrency": "USD", "billingDuration": "P1M"}},
            ],
        },
    ]
    return '<script type="application/ld+json">\n' + json.dumps(data, indent=2) + "\n</script>"


# ------------------------------------------------------------------ social card

def make_og_image() -> None:
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1200, 630
    img = Image.new("RGB", (W, H), "#0E0E10")
    d = ImageDraw.Draw(img)
    # faint blueprint grid
    for x in range(0, W, 40):
        d.line([(x, 0), (x, H)], fill="#17171a", width=1)
    for y in range(0, H, 40):
        d.line([(0, y), (W, y)], fill="#17171a", width=1)
    serif = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
    sans = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    big, mid, small = (ImageFont.truetype(serif, 150), ImageFont.truetype(serif, 54), ImageFont.truetype(sans, 30))
    x, y = 90, 150
    for part, color in (("Nit", "#FAFAF8"), ("o", "#E8601C"), ("CAD", "#FAFAF8")):
        d.text((x, y), part, font=big, fill=color)
        x += d.textlength(part, font=big)
    d.text((90, 360), "The engineer that checks", font=mid, fill="#FAFAF8")
    d.text((90, 428), "its own work.", font=mid, fill="#FAFAF8")
    d.text((90, 540), "Text-to-CAD  |  STEP files  |  PASS, FAIL or UNKNOWN", font=small, fill="#9A9AA0")
    img.save(FRONTEND / "og-image.png", optimize=True)


# ------------------------------------------------------------------ main

def main() -> None:
    make_og_image()

    for name, spec in PAGES.items():
        build_page(name, spec)

    # existing pages: head tags
    for page, path in INDEXED.items():
        title, desc = read_meta(page)
        if page == "index.html":
            title, desc = HOME_TITLE, HOME_DESC
            s = (FRONTEND / page).read_text()
            s = re.sub(r"<title>.*?</title>", f"<title>{esc(HOME_TITLE)}</title>", s, count=1, flags=re.S)
            s = re.sub(r'<meta name="description" content=".*?">', f'<meta name="description" content="{esc(HOME_DESC)}">', s, count=1, flags=re.S)
            s = re.sub(r'<script type="application/ld\+json">.*?</script>\n?', "", s, flags=re.S)
            (FRONTEND / page).write_text(s)
        if not (FRONTEND / page).exists():
            continue
        block = head_tags(path, title, desc)
        if page == "index.html":
            block += "\n" + home_jsonld()
        stamp(page, block)
    for page in NOINDEX:
        if (FRONTEND / page).exists():
            stamp(page, head_tags("/" + page, "", "", noindex=True))

    # robots.txt and sitemap.xml
    (FRONTEND / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    urls = "".join(
        f"  <url>\n    <loc>{SITE_URL}{path}</loc>\n    <lastmod>{TODAY}</lastmod>\n  </url>\n"
        for page, path in INDEXED.items() if (FRONTEND / page).exists()
    )
    (FRONTEND / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "</urlset>\n"
    )
    print(f"SEO files written for {SITE_URL}")


if __name__ == "__main__":
    main()
