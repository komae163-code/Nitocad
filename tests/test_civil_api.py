"""
HTTP tests for the /civil routes (civil_api.py), through the real app: sign-in,
validation, and the shape of each answer. The numbers themselves are tested in
test_civil_engineering.py, so these check the wiring and a few headline values.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.requires_cadquery

FLAT_SITE = "\n".join(f"{x},{y},100" for x in range(-100, 401, 25) for y in range(-100, 101, 25))
STRAIGHT = {"pis": [[0, 0], [300, 0]], "radii": [0, 0], "pvis": [[0, 101, 0], [300, 101, 0]]}
FLAT_TEMPLATE = {"crossfall_pct": 0, "shoulder_crossfall_pct": 0, "pavement_thickness_m": 0}
ROUTES = [
    ("get", "/civil/criteria/default", None),
    ("post", "/civil/terrain", {"text": "0,0,1\n10,0,2\n0,10,3"}),
    ("post", "/civil/alignment", {**STRAIGHT}),
    ("post", "/civil/hydrology/rational", {"runoff_coeff": 0.7, "area_ha": 5, "intensity_mm_h": 100}),
    ("post", "/civil/hydraulics/pipe", {"flow_m3s": 0.5, "slope": 0.005}),
    ("post", "/civil/hydraulics/channel", {"flow_m3s": 5, "bottom_width_m": 3, "slope": 0.001}),
    ("post", "/civil/hydraulics/culvert", {"flow_m3s": 3, "clear_width_m": 1.5, "clear_height_m": 1.2, "length_m": 12, "slope": 0.01}),
    ("post", "/civil/loads/culvert", {"clear_width_m": 1.5, "clear_height_m": 1.2, "wall_thickness_m": 0.2, "slab_thickness_m": 0.25}),
    ("post", "/civil/wall/stability", {"stem_height_m": 5.4, "stem_thickness_base_m": 0.5, "stem_thickness_top_m": 0.3,
                                       "base_width_m": 3.6, "base_thickness_m": 0.6, "toe_length_m": 0.8}),
]


def _call(client, method, path, body, headers=None):
    return client.get(path, headers=headers) if method == "get" else client.post(path, json=body, headers=headers)


class TestAccess:
    @pytest.mark.parametrize("method,path,body", ROUTES)
    def test_every_route_needs_a_signed_in_account(self, client, method, path, body):
        assert _call(client, method, path, body).status_code == 401

    @pytest.mark.parametrize("method,path,body", ROUTES)
    def test_every_route_answers_a_signed_in_account(self, client, wallet_session, method, path, body):
        r = _call(client, method, path, body, wallet_session["auth_headers"])
        assert r.status_code == 200, r.text


class TestRoutes:
    def test_default_criteria_are_labelled_as_not_a_standard(self, client, wallet_session):
        r = client.get("/civil/criteria/default", headers=wallet_session["auth_headers"]).json()
        assert r["criteria"]["max_grade_pct"] == 8.0 and "not a design standard" in r["criteria"]["name"]
        assert r["min_curve_radius_from_speed_m"] == pytest.approx(135.0, abs=0.1)

    def test_terrain_reports_the_surface_and_can_return_an_stl(self, client, wallet_session):
        r = client.post("/civil/terrain", json={"text": FLAT_SITE, "include_stl": True}, headers=wallet_session["auth_headers"]).json()
        assert r["terrain"]["z_min"] == r["terrain"]["z_max"] == 100 and r["terrain"]["plan_area_m2"] == pytest.approx(500 * 200)
        assert r["stl"].startswith("solid terrain")

    def test_terrain_rejects_unusable_survey_files_with_a_message(self, client, wallet_session):
        r = client.post("/civil/terrain", json={"text": "0,0,1\n1,1,2\n2,2,3\n3,3,4"}, headers=wallet_session["auth_headers"])
        assert r.status_code == 422 and "straight line" in r.json()["detail"]
        r = client.post("/civil/terrain", json={"text": "x,y\n1,2"}, headers=wallet_session["auth_headers"])
        assert r.status_code == 422 and "z" in r.json()["detail"]
        assert client.post("/civil/terrain", json={"text": ""}, headers=wallet_session["auth_headers"]).status_code == 422

    def test_alignment_gives_curves_stations_and_checks(self, client, wallet_session):
        body = {"pis": [[0, 0], [100, 0], [100, 100]], "radii": [0, 30, 0], "pvis": [[0, 100, 0], [100, 110, 40], [187.124, 100, 0]],
                "station_interval_m": 50}
        r = client.post("/civil/alignment", json=body, headers=wallet_session["auth_headers"]).json()
        assert r["length_m"] == pytest.approx(187.124, abs=1e-3)
        assert r["horizontal_curves"][0]["radius"] == 30 and r["vertical_curves"][0]["type"] == "crest"
        statuses = {c["check"]: c["status"] for c in r["checks"]["findings"]}
        assert statuses["curve radius at PI 2"] == "fail"          # 30 m is under the starter minimum
        assert "not a statement of compliance" in r["checks"]["statement"]

    def test_alignment_errors_come_back_as_422(self, client, wallet_session):
        body = {**STRAIGHT, "pvis": [[0, 100, 0], [100, 100, 0]]}
        r = client.post("/civil/alignment", json=body, headers=wallet_session["auth_headers"])
        assert r.status_code == 422 and "must cover" in r.json()["detail"]

    def test_earthworks_matches_the_hand_calculation(self, client, wallet_session):
        body = {"survey": {"text": FLAT_SITE}, "alignment": STRAIGHT, "template": FLAT_TEMPLATE, "station_interval_m": 50}
        r = client.post("/civil/earthworks", json=body, headers=wallet_session["auth_headers"])
        assert r.status_code == 200, r.text
        q = r.json()["quantities"]
        assert q["fill_m3"] == pytest.approx(12 * 300, rel=1e-6) and q["cut_m3"] == pytest.approx(0, abs=1e-6)
        assert r.json()["checks"]["criteria_name"].startswith("Generic starter")

    def test_earthworks_with_a_bad_template_is_a_422_not_a_500(self, client, wallet_session):
        body = {"survey": {"text": FLAT_SITE}, "alignment": STRAIGHT, "template": {"lane_count": 99}}
        assert client.post("/civil/earthworks", json=body, headers=wallet_session["auth_headers"]).status_code == 422

    def test_rational_method_and_its_input_rules(self, client, wallet_session):
        h = wallet_session["auth_headers"]
        r = client.post("/civil/hydrology/rational", json={"runoff_coeff": 0.7, "area_ha": 5, "intensity_mm_h": 100}, headers=h).json()
        assert r["flow_m3s"] == pytest.approx(0.7 * 100 * 5 / 360)
        r = client.post("/civil/hydrology/rational", json={"runoff_coeff": 0.7, "area_ha": 5, "idf": [1000, 10, 0.8], "duration_min": 20}, headers=h).json()
        assert r["intensity_mm_h"] == pytest.approx(1000 / 30 ** 0.8)
        bad = client.post("/civil/hydrology/rational", json={"runoff_coeff": 0.7, "area_ha": 5}, headers=h)
        assert bad.status_code == 422 and "intensity" in bad.json()["detail"]

    def test_pipe_is_sized_or_checked(self, client, wallet_session):
        h = wallet_session["auth_headers"]
        sized = client.post("/civil/hydraulics/pipe", json={"flow_m3s": 0.5, "slope": 0.005}, headers=h).json()
        assert sized["selected_diameter_mm"] == 750 and sized["checks"]["failed"] == 0
        small = client.post("/civil/hydraulics/pipe", json={"flow_m3s": 5, "slope": 0.001, "diameter_mm": 300}, headers=h).json()
        assert small["status"] == "exceeds_capacity" and small["checks"]["failed"] >= 1

    def test_channel_and_culvert(self, client, wallet_session):
        h = wallet_session["auth_headers"]
        ch = client.post("/civil/hydraulics/channel", json={"flow_m3s": 5, "bottom_width_m": 3, "slope": 0.001}, headers=h).json()
        assert ch["regime"] == "subcritical" and ch["normal_depth_m"] > 0
        cv = client.post("/civil/hydraulics/culvert", json={"flow_m3s": 3, "clear_width_m": 1.5, "clear_height_m": 1.2, "length_m": 12, "slope": 0.01}, headers=h).json()
        assert cv["inlet"]["regime"].startswith("unsubmerged") and cv["barrel"]["status"] == "ok"

    def test_wall_stability_and_custom_criteria(self, client, wallet_session):
        h = wallet_session["auth_headers"]
        wall = {"stem_height_m": 5.4, "stem_thickness_base_m": 0.5, "stem_thickness_top_m": 0.3,
                "base_width_m": 3.6, "base_thickness_m": 0.6, "toe_length_m": 0.8}
        r = client.post("/civil/wall/stability", json=wall, headers=h).json()
        status = {c["check"]: c["status"] for c in r["checks"]["findings"]}
        assert status["sliding"] == "fail" and status["overturning"] == "pass"
        relaxed = client.post("/civil/wall/stability", json={**wall, "criteria": {"wall_min_fs_sliding": 1.0, "name": "Project X"}}, headers=h).json()
        assert {c["check"]: c["status"] for c in relaxed["checks"]["findings"]}["sliding"] == "pass"
        assert relaxed["checks"]["criteria_name"] == "Project X"

    def test_unknown_or_malformed_criteria_are_rejected(self, client, wallet_session):
        h = wallet_session["auth_headers"]
        body = {"flow_m3s": 5, "bottom_width_m": 3, "slope": 0.001}
        assert client.post("/civil/hydraulics/channel", json={**body, "criteria": {"nope": 1}}, headers=h).status_code == 422
        assert client.post("/civil/hydraulics/channel", json={**body, "criteria": {"channel_max_froude": "x"}}, headers=h).status_code == 422

    def test_impossible_wall_geometry_is_a_422(self, client, wallet_session):
        wall = {"stem_height_m": 5, "stem_thickness_base_m": 0.5, "stem_thickness_top_m": 0.9,
                "base_width_m": 3, "base_thickness_m": 0.5, "toe_length_m": 0.5}
        r = client.post("/civil/wall/stability", json=wall, headers=wallet_session["auth_headers"])
        assert r.status_code == 422 and "thicker" in r.json()["detail"]
