"""
External asset providers: fetch a part on demand instead of modelling everything from scratch.

Three kinds of record, and they are NOT interchangeable:
  parametric  a generator you run (e.g. cq_warehouse screws). Real solids, editable, can be STEP/BOM/validated.
  cad_file    a STEP/FCStd file someone else modelled. Real solids, but fixed dimensions.
  mesh        a visual mesh (USD/GLB). Reference only: not editable, not measured, not in mass or fit checks.

Rules this module enforces:
  * Every record carries its licence. A provider whose licence is not confirmed is BLOCKED, and fetch()
    raises LicenceBlocked. Nothing is downloaded "just to see".
  * Downloads go through an HTTPS host allowlist, a size cap and a sanitised file name, into a cache folder,
    with a `<file>.source.json` next to it (url, licence, attribution, sha256).
  * The network is injected (`http_get`), so everything is testable offline. The real-network paths here
    have NOT been run against the live services.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

HttpGet = Callable[[str], bytes]
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class LicenceBlocked(Exception):
    """The provider's licence is unconfirmed or forbids this use."""


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class AssetRecord:
    id: str
    name: str
    provider: str
    kind: str                       # parametric | cad_file | mesh
    format: str                     # e.g. "usd", "step", "python"
    licence: str                    # SPDX-style id, or "UNCONFIRMED"
    attribution_text: str = ""      # what must be credited ("" = nothing required)
    url: str = ""
    tags: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    @property
    def reference_only(self) -> bool:
        return self.kind == "mesh"


def _tokens(s: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]


def _matches(record_name: str, query: str) -> bool:
    have = set(_tokens(record_name))
    return all(any(q == h or h.startswith(q) for h in have) for q in _tokens(query))


def default_http_get(url: str, *, allowed_hosts: tuple[str, ...], max_bytes: int = MAX_DOWNLOAD_BYTES,
                     timeout: float = 30.0) -> bytes:
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or p.hostname not in allowed_hosts:
        raise FetchError(f"blocked URL (https and an allowlisted host only): {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "NitoCAD-asset-fetch/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:                       # noqa: S310 (scheme checked above)
        if p.hostname not in allowed_hosts or not r.geturl().startswith("https://"):
            raise FetchError("redirected off https")
        data = r.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise FetchError(f"download larger than the {max_bytes} byte cap")
    return data


class Provider:
    name = "base"
    licence = "UNCONFIRMED"
    blocked_reason: str | None = None          # set to a sentence to block the whole provider
    hosts: tuple[str, ...] = ()

    def __init__(self, http_get: HttpGet | None = None):
        self._get = http_get or (lambda u: default_http_get(u, allowed_hosts=self.hosts))
        self.notes: list[str] = []      # things a search could not do (e.g. a truncated index); the registry reports them

    def search(self, query: str, limit: int = 20) -> list[AssetRecord]:
        raise NotImplementedError

    def fetch(self, rec: AssetRecord, dest_dir: Path) -> dict[str, Any]:
        if self.blocked_reason:
            raise LicenceBlocked(f"{self.name}: {self.blocked_reason}")
        return self._fetch(rec, Path(dest_dir))

    def _fetch(self, rec: AssetRecord, dest_dir: Path) -> dict[str, Any]:
        raise NotImplementedError


# ---------------------------------------------------------------------------------------------------
class ParametricCatalog(Provider):
    """cq_warehouse generators. Nothing is downloaded; fetch() returns the import recipe.
    Licence Apache-2.0 (per the AUR package metadata; confirm in the repository before shipping)."""
    name = "cq_warehouse"
    licence = "Apache-2.0"
    _CLASSES = {
        "fastener": ["SocketHeadCapScrew", "ButtonHeadScrew", "ButtonHeadWithCollarScrew", "CheeseHeadScrew",
                     "HexNut", "HexNutWithFlange", "UnchamferedHexagonNut", "SquareNut", "DomedCapNut"],
        "bearing": ["SingleRowDeepGrooveBallBearing", "SingleRowCappedDeepGrooveBallBearing",
                    "SingleRowAngularContactBallBearing", "SingleRowCylindricalRollerBearing",
                    "SingleRowTaperedRollerBearing"],
        "thread": ["IsoThread", "AcmeThread", "MetricTrapezoidalThread"],
        "chain": ["Chain"],
        "sprocket": ["Sprocket"],
    }

    def search(self, query: str, limit: int = 20) -> list[AssetRecord]:
        out = []
        for module, classes in self._CLASSES.items():
            for c in classes:
                if _matches(c, query) or _matches(module, query):
                    out.append(AssetRecord(id=f"{module}.{c}", name=c, provider=self.name, kind="parametric",
                                           format="python", licence=self.licence, tags=(module,),
                                           extra={"module": module, "class": c}))
        return out[:limit]

    def _fetch(self, rec: AssetRecord, dest_dir: Path) -> dict[str, Any]:
        m, c = rec.extra["module"], rec.extra["class"]
        return {"record": asdict(rec), "file": None,
                "recipe": f"from cq_warehouse.{m} import {c}",
                "requires": "cq_warehouse (pip install git+https://github.com/gumyr/cq_warehouse.git); "
                            "not yet in this project's requirements and not yet tested against your CadQuery version"}


class NvidiaSimReady(Provider):
    """nvidia/simready-assets on Hugging Face. USD visual/physics meshes, CC-BY-4.0 (credit NVIDIA)."""
    name = "nvidia_simready"
    licence = "CC-BY-4.0"
    hosts = ("huggingface.co",)
    _REPO = "nvidia/simready-assets"

    def _index(self) -> list[str]:
        raw = self._get(f"https://huggingface.co/api/datasets/{self._REPO}/tree/main")
        try:
            items = json.loads(raw)
        except ValueError as e:
            raise FetchError(f"unreadable index from Hugging Face: {e}") from None
        return sorted(i["path"] for i in items if isinstance(i, dict) and i.get("type") == "directory"
                      and re.fullmatch(r"[A-Za-z0-9_]+", i.get("path", "")))

    def search(self, query: str, limit: int = 20) -> list[AssetRecord]:
        out = []
        for d in self._index():
            if _matches(d, query):
                path = f"{d}/simready_usd/sm_{d}_01.usd"          # pattern seen on the dataset page; verify per asset
                out.append(AssetRecord(
                    id=d, name=d, provider=self.name, kind="mesh", format="usd", licence=self.licence,
                    attribution_text="NVIDIA SimReady Assets (huggingface.co/datasets/nvidia/simready-assets), CC BY 4.0",
                    url=f"https://huggingface.co/datasets/{self._REPO}/resolve/main/{path}", tags=("usd", "reference")))
        return out[:limit]

    def _fetch(self, rec: AssetRecord, dest_dir: Path) -> dict[str, Any]:
        return _download(self._get, rec, dest_dir)


class Polyfork(Provider):
    """polyfork.dev low-poly assets. BLOCKED: its licence forbids use in a commercial asset-generating service
    and forbids redistributing the files. NitoCAD is such a service, so this stays off until Polyfork
    confirms in writing (they also sell a platform plan for products that serve assets to their own users)."""
    name = "polyfork"
    licence = "UNCONFIRMED"
    blocked_reason = ("licence unconfirmed: it forbids commercial asset-generator pipelines and redistribution; "
                      "get written permission or a platform plan first")

    def search(self, query: str, limit: int = 20) -> list[AssetRecord]:
        return []



class FreeCADLibrary(Provider):
    """FreeCAD/FreeCAD-library on GitHub: community STEP parts. CC-BY 3.0, and EACH part's own author(s) must be
    credited (the repository says to see the commit history). Fetch resolves the authors from the commits API;
    if that fails the record is marked UNRESOLVED and the packager refuses to call the package clean.
    Unauthenticated GitHub API calls are rate-limited (about 60 per hour), so the index is cached per instance."""
    name = "freecad_library"
    licence = "CC-BY-3.0"
    hosts = ("api.github.com", "raw.githubusercontent.com")
    _REPO = "FreeCAD/FreeCAD-library"
    _BRANCH = "master"
    _OK_PATH = re.compile(r"^[A-Za-z0-9 _().,+\-/]+\.(step|stp)$", re.I)

    def __init__(self, http_get: HttpGet | None = None):
        super().__init__(http_get)
        self._paths: list[str] | None = None

    def _index(self) -> list[str]:
        if self._paths is None:
            raw = self._get(f"https://api.github.com/repos/{self._REPO}/git/trees/{self._BRANCH}?recursive=1")
            try:
                data = json.loads(raw)
            except ValueError as e:
                raise FetchError(f"unreadable index from GitHub: {e}") from None
            if data.get("truncated"):
                self.notes.append("GitHub truncated the file list, so some parts may not be searchable")
            self._paths = sorted(t["path"] for t in data.get("tree", [])
                                 if t.get("type") == "blob" and ".." not in t["path"] and self._OK_PATH.match(t["path"]))
        return self._paths

    def search(self, query: str, limit: int = 20) -> list[AssetRecord]:
        out = []
        for path in self._index():
            if _matches(path, query):
                stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                out.append(AssetRecord(
                    id=path, name=stem, provider=self.name, kind="cad_file", format=path.rsplit(".", 1)[-1].lower(),
                    licence=self.licence, tags=tuple(path.split("/")[:-1]),
                    attribution_text=f"UNRESOLVED: author(s) of {path} (resolved at fetch)",
                    url=f"https://raw.githubusercontent.com/{self._REPO}/{self._BRANCH}/" + urllib.parse.quote(path)))
        return out[:limit]

    def _authors(self, path: str) -> list[str]:
        raw = self._get(f"https://api.github.com/repos/{self._REPO}/commits?per_page=100&path=" + urllib.parse.quote(path))
        names = []
        for c in json.loads(raw):
            n = ((c.get("commit") or {}).get("author") or {}).get("name")
            if n and n not in names:
                names.append(n)
        return names

    def _fetch(self, rec: AssetRecord, dest_dir: Path) -> dict[str, Any]:
        try:
            names = self._authors(rec.id)
        except Exception:                                  # network, JSON or rate-limit trouble: fail closed, not silent
            names = []
        if names:
            text = (f"{rec.name} by {', '.join(names)}; from FreeCAD-library "
                    "(github.com/FreeCAD/FreeCAD-library), CC BY 3.0")
        else:
            text = f"UNRESOLVED: could not read the author(s) of {rec.id}; look them up before use"
        return _download(self._get, replace(rec, attribution_text=text), dest_dir)


class _Gated(Provider):
    """A source we know about but will not fetch from, with the reason on record. It shows up in search notes
    so nobody wonders why a source is missing. No fetch URL is invented for any of these."""
    def search(self, query: str, limit: int = 20) -> list[AssetRecord]:
        return []


class OpenBuilds(_Gated):
    name = "openbuilds"
    blocked_reason = ("terms read as personal use with no warranty; get their written permission before using the "
                      "models in a product (one forum post also reports a bearing dimension mismatch, so measure first)")


class KiCad3D(_Gated):
    name = "kicad_3d"
    licence = "CC-BY-SA-4.0"
    blocked_reason = ("CC-BY-SA 4.0: using a model inside a design is allowed, but redistributing the models as a "
                      "collection must stay CC-BY-SA, which a downloadable package could trigger; needs a licence review. "
                      "Also electronics only, and no verified download URL yet")


class TraceParts(_Gated):
    name = "traceparts"
    blocked_reason = ("the API is sold to parts vendors under a business contract; free engineer downloads are for "
                      "people, not for a product to fetch on its own")


def _download(get: HttpGet, rec: AssetRecord, dest_dir: Path) -> dict[str, Any]:
    if not rec.url:
        raise FetchError("record has no URL")
    dest_dir.mkdir(parents=True, exist_ok=True)
    data = get(rec.url)
    if not data:
        raise FetchError("empty download")
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise FetchError("download larger than the size cap")
    if rec.format in ("step", "stp") and b"ISO-10303-21" not in data[:200]:
        raise FetchError("downloaded file is not a STEP file (an error page, or the wrong URL)")
    fname = _SAFE_NAME.sub("_", f"{rec.provider}__{rec.id}.{rec.format}")[:120]
    path = dest_dir / fname
    path.write_bytes(data)
    side = {"id": rec.id, "provider": rec.provider, "url": rec.url, "licence": rec.licence,
            "attribution_text": rec.attribution_text, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "kind": rec.kind}
    (dest_dir / (fname + ".source.json")).write_text(json.dumps(side, indent=2), encoding="utf-8")
    return {"record": asdict(rec), "file": str(path), "source": side}


# ---------------------------------------------------------------------------------------------------
class Registry:
    def __init__(self, providers: list[Provider]):
        self.providers = {p.name: p for p in providers}

    def search(self, query: str, *, kinds: tuple[str, ...] | None = None, limit: int = 20) -> dict[str, Any]:
        found, notes = [], []
        for p in self.providers.values():
            p.notes = []
            if p.blocked_reason:
                notes.append(f"{p.name}: skipped ({p.blocked_reason})")
                continue
            try:
                found += [r for r in p.search(query, limit) if kinds is None or r.kind in kinds]
            except Exception as e:                                           # one bad provider must not hide the rest
                notes.append(f"{p.name}: search failed ({type(e).__name__}: {str(e)[:100]})")
            notes += [f"{p.name}: {n}" for n in p.notes]
        order = {"parametric": 0, "cad_file": 1, "mesh": 2}                  # prefer editable over reference
        found.sort(key=lambda r: (order.get(r.kind, 9), r.name))
        return {"results": found[:limit], "notes": notes}

    def fetch(self, rec: AssetRecord, dest_dir: str | Path) -> dict[str, Any]:
        if rec.provider not in self.providers:
            raise FetchError(f"unknown provider {rec.provider!r}")
        return self.providers[rec.provider].fetch(rec, Path(dest_dir))


def default_registry(http_get: HttpGet | None = None) -> Registry:
    return Registry([ParametricCatalog(), FreeCADLibrary(http_get), NvidiaSimReady(http_get),
                     Polyfork(), OpenBuilds(), KiCad3D(), TraceParts()])


def as_part(fetched: dict[str, Any], name: str) -> dict[str, Any]:
    """Shape a fetched record like the rest of NitoCAD's parts list. Meshes are marked reference-only so the
    BOM, mass and fit checks do not treat them as real geometry."""
    rec = fetched["record"]
    return {"name": name, "part_type": "external_" + rec["kind"], "material": None,
            "parameters": {"source": rec["provider"], "id": rec["id"], "licence": rec["licence"],
                           "file": fetched.get("file"), "recipe": fetched.get("recipe")},
            "reference_only": rec["kind"] == "mesh"}


def notices(fetched: list[dict[str, Any]]) -> str:
    """Text for a NOTICES file: one line per external asset that needs credit, plus a line for the rest."""
    lines, plain = [], 0
    for f in fetched:
        r = f["record"]
        if r["attribution_text"]:
            lines.append(f"- {r['name']} ({r['provider']}, {r['licence']}): {r['attribution_text']}")
        else:
            plain += 1
    if plain:
        lines.append(f"- {plain} further external item(s) with no attribution requirement recorded.")
    return "Third-party assets used in this package\n\n" + ("\n".join(lines) if lines else "(none)") + "\n"
