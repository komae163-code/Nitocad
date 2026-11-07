"""Standard parts for model-written code: search, fetch and pin library files OUTSIDE the sandbox.

Model code asks for parts declaratively (`LIBRARY = {"bearing": {"query": "608 bearing"}}`, see codegen_check.py). This module
turns each request into a STEP file using asset_providers.py, which already enforces the rules that matter:
  * an unconfirmed licence blocks the provider, nothing is downloaded "just to see"
  * HTTPS and an allowlisted host only, a size cap, a sanitised file name
  * the author credit that CC-BY needs is looked up per part, and marked UNRESOLVED when it cannot be

Only editable solids are used (STEP, kind "cad_file"). Meshes are reference-only and parametric generators (cq_warehouse) need
that package installed, so both are skipped here.

Pinning: every resolved part is recorded with its URL and SHA-256. When a saved model is rebuilt or exported the file is read from
the cache, or downloaded again and checked against the recorded hash, so a model never silently changes because someone edited a
library file upstream.

The sandbox never sees a URL or a network. It gets a file in its own work folder, imports it, and passes the solid to `build`.
Real-network behaviour of the providers has NOT been run against the live services (see asset_providers.py).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import asset_providers as AP

MAX_PART_BYTES = int(os.environ.get("CODEGEN_PART_MAX_MB", "30")) * 1024 * 1024
UNRESOLVED = "UNRESOLVED"


def default_cache_dir() -> Path:
    """CODEGEN_PART_CACHE if set, else the asset cache the vehicle builder uses, so one download serves both."""
    explicit = os.environ.get("CODEGEN_PART_CACHE", "")
    if explicit:
        return Path(explicit)
    return Path(os.environ.get("NITOCAD_ASSET_CACHE") or (Path(__file__).resolve().parent / "data" / "asset_cache"))


class PartResolver:
    def __init__(self, registry: AP.Registry | None = None, cache_dir: Path | str | None = None, *, http_get: AP.HttpGet | None = None):
        # Only the STEP library is searched: the other providers cost network calls and cannot return an editable solid.
        self._registry = registry or AP.Registry([AP.FreeCADLibrary(http_get)])
        self._cache = Path(cache_dir) if cache_dir else default_cache_dir()

    # ------------------------------------------------------------------ public async API
    async def resolve(self, spec: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """{"ok": True, "parts": {name: {"path", "scale"}}, "entries": [...]} or {"ok": False, "error": str}."""
        return await asyncio.to_thread(self._resolve_sync, spec)

    async def restore(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        """Same shape as resolve(), from entries saved earlier. Re-downloads when the cache lost a file or a credit line was unresolved,
        and always checks the SHA-256 recorded when the model was made."""
        return await asyncio.to_thread(self._restore_sync, entries)

    # ------------------------------------------------------------------ helpers
    def _file_for(self, rec: AP.AssetRecord) -> Path:
        return self._cache / AP._cache_name(rec)          # the same name asset_providers writes, so its cache is found

    @staticmethod
    def _sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _entry(self, name: str, query: str, scale: float, rec_dict: dict[str, Any], path: Path, sha: str) -> dict[str, Any]:
        text = rec_dict.get("attribution_text") or ""
        return {"name": name, "query": query, "scale": scale, "provider": rec_dict["provider"], "id": rec_dict["id"],
                "part_name": rec_dict["name"], "format": rec_dict["format"], "licence": rec_dict["licence"], "url": rec_dict["url"],
                "sha256": sha, "attribution": text, "credit_resolved": not text.startswith(UNRESOLVED)}

    def _cached_attribution(self, path: Path) -> str | None:
        side = Path(str(path) + ".source.json")
        try:
            return json.loads(side.read_text(encoding="utf-8")).get("attribution_text")
        except (OSError, ValueError):
            return None

    def _get_file(self, rec: AP.AssetRecord, *, refresh: bool) -> tuple[Path, dict[str, Any], str]:
        """(path, record dict with attribution, sha256). Uses the cache unless refresh or the credit was unresolved."""
        path = self._file_for(rec)
        if not refresh and path.is_file():
            text = self._cached_attribution(path)
            if text and not text.startswith(UNRESOLVED):
                d = asdict(rec)
                d["attribution_text"] = text
                return path, d, self._sha(path)
        fetched = self._registry.fetch(rec, self._cache)
        path = Path(fetched["file"])
        return path, fetched["record"], self._sha(path)

    def _check_size(self, path: Path, label: str) -> str | None:
        if path.stat().st_size > MAX_PART_BYTES:
            return f"The library part for '{label}' is larger than {MAX_PART_BYTES // (1024 * 1024)} MB. Model it yourself."
        return None

    # ------------------------------------------------------------------ sync implementations
    def _resolve_sync(self, spec: dict[str, dict[str, Any]]) -> dict[str, Any]:
        parts: dict[str, dict[str, Any]] = {}
        entries: list[dict[str, Any]] = []
        for name, req in spec.items():
            query, scale = req["query"], float(req.get("scale", 1.0))
            try:
                found = self._registry.search(query, kinds=("cad_file",), limit=5)
            except Exception as e:                                                      # noqa: BLE001
                return {"ok": False, "error": f"The part library could not be searched for '{name}' ({type(e).__name__}). Model it yourself."}
            hits = [r for r in found["results"] if r.format in ("step", "stp")]
            if not hits:
                return {"ok": False, "error": f"No library part matches '{query}' (for '{name}'). Model it yourself, or use different words "
                                              "(for example the part type and its standard size)."}
            rec = hits[0]
            try:
                path, rec_dict, sha = self._get_file(rec, refresh=False)
            except AP.LicenceBlocked as e:
                return {"ok": False, "error": f"The library part for '{name}' cannot be used: {e}. Model it yourself."}
            except Exception as e:                                                      # noqa: BLE001  FetchError, network, disk
                return {"ok": False, "error": f"The library part for '{name}' could not be downloaded ({type(e).__name__}). Model it yourself."}
            too_big = self._check_size(path, name)
            if too_big:
                return {"ok": False, "error": too_big}
            parts[name] = {"path": str(path), "scale": scale}
            entries.append(self._entry(name, query, scale, rec_dict, path, sha))
        return {"ok": True, "parts": parts, "entries": entries}

    def _restore_sync(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        parts: dict[str, dict[str, Any]] = {}
        updated: list[dict[str, Any]] = []
        for e in entries:
            try:
                rec = AP.AssetRecord(id=e["id"], name=e.get("part_name") or e["id"], provider=e["provider"], kind="cad_file",
                                     format=e["format"], licence=e["licence"], url=e["url"], attribution_text=e.get("attribution", ""))
                refresh = not e.get("credit_resolved", False)
                path, rec_dict, sha = self._get_file(rec, refresh=refresh)
                if sha != e["sha256"]:
                    path2 = None
                    if not refresh:                                           # a corrupt cache file: try one clean download
                        path2, rec_dict, sha = self._get_file(rec, refresh=True)
                    if path2 is None or sha != e["sha256"]:
                        return {"ok": False, "error": f"The library file for '{e['name']}' has changed since this model was made, so it cannot "
                                                      "be rebuilt exactly. Generate the model again."}
                    path = path2
            except AP.LicenceBlocked as ex:
                return {"ok": False, "error": f"The library part for '{e.get('name')}' can no longer be used: {ex}."}
            except Exception as ex:                                                     # noqa: BLE001
                return {"ok": False, "error": f"The library part for '{e.get('name')}' could not be loaded ({type(ex).__name__}). Try again."}
            too_big = self._check_size(path, e["name"])
            if too_big:
                return {"ok": False, "error": too_big}
            parts[e["name"]] = {"path": str(path), "scale": float(e.get("scale", 1.0))}
            ne = self._entry(e["name"], e.get("query", ""), float(e.get("scale", 1.0)), rec_dict, path, sha)
            updated.append(ne)
        return {"ok": True, "parts": parts, "entries": updated}


def credit_blockers(entries: list[dict[str, Any]]) -> list[str]:
    """Names of library parts whose author credit could not be looked up. Files that leave the app must not go out without it."""
    return [e["name"] for e in entries if not e.get("credit_resolved")]


def notices(entries: list[dict[str, Any]]) -> str:
    """Text for a NOTICES.txt: one line per part with its licence and the credit that licence requires."""
    if not entries:
        return ""
    lines = ["This model includes parts from open libraries. Keep these credits with the files.", ""]
    for e in entries:
        credit = e.get("attribution") or "no credit text recorded"
        lines.append(f"- {e.get('part_name') or e['name']} ({e['provider']}, {e['licence']}): {credit}\n  source: {e['url']}")
    return "\n".join(lines) + "\n"
