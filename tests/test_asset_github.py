import json
import os
import tempfile
import unittest
import urllib.request

import asset_providers as AP

PATHS = ["Generic objects/Scale Models/Cement mixer truck/wood_screw.stp",
         "Mechanical Parts/Fasteners/Screws/ISO4762 M3x10.step"]


def tree_bytes():
    return json.dumps({"tree": [{"type": "blob", "path": p} for p in PATHS]}).encode()


def set_env(name, value):
    old = os.environ.get(name)
    os.environ[name] = value
    return old


def restore_env(name, old):
    if old is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = old


class GithubSourceTests(unittest.TestCase):
    def test_folder_match_beats_a_stray_name(self):
        lib = AP.FreeCADLibrary(http_get=lambda u: tree_bytes())
        self.assertEqual(lib.search("screw")[0].id, PATHS[1])

    def test_token_goes_to_the_api_host_only_and_is_not_forwarded(self):
        old = set_env("NITOCAD_GITHUB_TOKEN", "secret")
        try:
            api = urllib.request.Request("https://api.github.com/x")
            AP._sign(api, "api.github.com")
            raw = urllib.request.Request("https://raw.githubusercontent.com/x")
            AP._sign(raw, "raw.githubusercontent.com")
        finally:
            restore_env("NITOCAD_GITHUB_TOKEN", old)
        self.assertEqual(api.get_header("Authorization"), "Bearer secret")
        self.assertNotIn("Authorization", api.headers)
        self.assertFalse(raw.has_header("Authorization"))

    def test_listing_is_shared_between_builds_and_restarts(self):
        calls = []

        def fake(url, **kw):
            calls.append(url)
            return tree_bytes()

        real, AP.default_http_get = AP.default_http_get, fake
        old = set_env("NITOCAD_ASSET_CACHE", tempfile.mkdtemp())
        AP.FreeCADLibrary._SHARED.clear()
        try:
            AP.FreeCADLibrary().search("screw")
            AP.FreeCADLibrary().search("screw")
            self.assertEqual(len(calls), 1)
            AP.FreeCADLibrary._SHARED.clear()             # a restarted server reads the saved file instead
            AP.FreeCADLibrary().search("screw")
            self.assertEqual(len(calls), 1)
        finally:
            AP.default_http_get = real
            restore_env("NITOCAD_ASSET_CACHE", old)
            AP.FreeCADLibrary._SHARED.clear()


if __name__ == "__main__":
    unittest.main()
