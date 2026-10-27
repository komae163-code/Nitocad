"""HTTP tests for the PCB routes (pcb_api.py): the free preview, the block list, and the package
downloads for paid Studio PCB parts. Uses the shared fixtures (see conftest.py).

If kicad-cli is installed these also run KiCad's real checks and Gerber export for the package."""
from __future__ import annotations

import json

import pytest

import db

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

DESC = "5V board with a 555 blinking LED"


def _make_job(wallet, *, paid: bool, description: str = DESC, part_type: str = "pcb_board") -> str:
    """A stored Studio PCB part, as chat_service would leave it: a free preview (draft) until paid."""
    job_id = db.record_job(
        description=description, success=True, user_id=wallet["wallet_address"], part_type=part_type,
        parameters={"description": description}, draft=True, state="settled")
    if paid:
        status, _ = db.charge_job(job_id, wallet["wallet_address"], cost=1)
        assert status in ("charged", "already_paid")
    return job_id


class TestPublicRoutes:
    def test_block_library_is_listed(self, client):
        r = client.get("/pcb/blocks")
        assert r.status_code == 200
        types = {b["type"] for b in r.json()["blocks"]}
        assert {"ldo_3v3", "tlc555_blinker", "mosfet_switch", "buck_lm2596"} <= types

    def test_free_preview_returns_an_svg(self, client):
        r = client.post("/pcb/preview", json={"description": "5V board with a status LED"})
        assert r.status_code == 200, r.text
        assert "<svg" in r.json()["svg"]
        assert r.json()["report"]["routed"] is True

    def test_preview_explains_what_it_cannot_build(self, client):
        r = client.post("/pcb/preview", json={"description": "wifi thermostat with an OLED"})
        assert r.status_code == 422
        assert "cannot build" in r.json()["detail"][0]


class TestPackageRoutes:
    def test_needs_a_session(self, client):
        assert client.post("/pcb/jobs/anything/package").status_code in (401, 403)

    def test_a_free_preview_is_locked_until_generate(self, client, wallet_with_credits):
        job = _make_job(wallet_with_credits, paid=False)
        r = client.post(f"/pcb/jobs/{job}/package", headers=wallet_with_credits["auth_headers"])
        assert r.status_code == 402

    def test_paid_part_builds_and_downloads(self, client, wallet_with_credits):
        h = wallet_with_credits["auth_headers"]
        job = _make_job(wallet_with_credits, paid=True)
        r = client.post(f"/pcb/jobs/{job}/package", headers=h)
        assert r.status_code == 200, r.text
        assert {"svg", "bom", "kicad", "report"} <= set(r.json()["files"])
        link = client.post(f"/pcb/jobs/{job}/link", json={"kind": "bom"}, headers=h)
        assert link.status_code == 200, link.text
        f = client.get(link.json()["url"])
        assert f.status_code == 200 and "Designators" in f.text

    def test_gerbers_say_why_when_kicad_is_missing(self, client, wallet_with_credits):
        h = wallet_with_credits["auth_headers"]
        job = _make_job(wallet_with_credits, paid=True)
        r = client.post(f"/pcb/jobs/{job}/link", json={"kind": "gerbers"}, headers=h)
        if r.status_code == 200:                    # KiCad is installed here: the real file is offered
            assert client.get(r.json()["url"]).status_code == 200
        else:
            assert r.status_code == 422 and "KiCad" in r.json()["detail"]

    def test_other_wallets_other_kinds_and_bad_tokens_get_404(self, client, wallet_with_credits):
        h = wallet_with_credits["auth_headers"]
        job = _make_job(wallet_with_credits, paid=True)
        assert client.post(f"/pcb/jobs/{job}/link", json={"kind": "../../etc/passwd"}, headers=h).status_code == 404
        assert client.get(f"/pcb/jobs/{job}/file?kind=bom&t=nope").status_code == 404
        mech = _make_job(wallet_with_credits, paid=True, part_type="l_bracket")
        assert client.post(f"/pcb/jobs/{mech}/package", headers=h).status_code == 404   # not a PCB job

    def test_unbuildable_board_is_reported_not_crashed(self, client, wallet_with_credits):
        job = _make_job(wallet_with_credits, paid=True, description="wifi thermostat")
        r = client.post(f"/pcb/jobs/{job}/package", headers=wallet_with_credits["auth_headers"])
        assert r.status_code == 422
        assert "cannot build" in r.json()["detail"]
