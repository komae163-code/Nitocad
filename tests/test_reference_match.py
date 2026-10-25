"""Reference match score: voxel overlap of two closed meshes. Pure numpy, no CadQuery."""
import numpy as np

import reference_match as rm


def box(dx, dy, dz, at=(0, 0, 0)):
    x, y, z = at
    v = np.array([[x, y, z], [x + dx, y, z], [x + dx, y + dy, z], [x, y + dy, z],
                  [x, y, z + dz], [x + dx, y, z + dz], [x + dx, y + dy, z + dz], [x, y + dy, z + dz]], dtype=float)
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return v[np.array(f)]


def wedge(dx, dy, dz):
    """A triangular prism: half of the box with the same bounding box."""
    p = np.array([[0, 0, 0], [dx, 0, 0], [0, dy, 0], [0, 0, dz], [dx, 0, dz], [0, dy, dz]], dtype=float)
    f = [(0, 2, 1), (3, 4, 5), (0, 1, 4), (0, 4, 3), (1, 2, 5), (1, 5, 4), (2, 0, 3), (2, 3, 5)]
    return p[np.array(f)]


def test_there_are_24_distinct_turns():
    assert len(rm.ROTATIONS) == 24
    assert len({tuple(r.flatten()) for r in rm.ROTATIONS}) == 24


def test_voxelizing_a_box_gives_its_volume():
    t = box(20, 10, 5)
    lo, hi = rm._bbox(t)
    step, shape, origin = rm._grid(lo, hi, 5, 200_000)
    vol = rm.voxelize(t, origin, step, shape).sum() * step ** 3
    assert abs(vol - 1000) / 1000 < 0.05


def test_the_same_part_scores_close_to_100():
    r = rm.compare(box(40, 30, 6), box(40, 30, 6, at=(100, -50, 7)))   # same solid somewhere else
    assert r["match_percent"] > 97 and r["verdict"] == "Very close"
    assert r["overlap_volume_mm3"] > 0.95 * 40 * 30 * 6


def test_a_part_turned_a_quarter_turn_still_matches():
    r = rm.compare(box(40, 20, 6), box(20, 40, 6))
    assert r["match_percent"] > 95
    assert r["size_reference_mm"] == [40.0, 20.0, 6.0]


def test_a_half_size_part_overlaps_an_eighth():
    r = rm.compare(box(40, 40, 40), box(20, 20, 20))
    assert 10.0 < r["match_percent"] < 15.0 and r["verdict"] == "Different"
    assert abs(r["volume_ratio"] - 8.0) < 0.01


def test_a_wedge_in_a_box_scores_about_half():
    r = rm.compare(box(30, 30, 10), wedge(30, 30, 10))
    assert 44 < r["match_percent"] < 56


def test_a_thin_plate_is_not_lost_between_cells():
    r = rm.compare(box(100, 80, 2), box(100, 80, 2))
    assert r["match_percent"] > 95 and r["cell_mm"] <= 2 / rm.MIN_CELLS_ACROSS_THIN + 1e-9


def test_a_reference_in_inches_is_flagged_not_rescaled():
    r = rm.compare(box(50.8, 25.4, 12.7), box(2, 1, 0.5))
    assert r["match_percent"] < 5
    assert "inches" in r["notes"][0]


def test_unit_hint_stays_quiet_when_sizes_agree():
    assert rm._unit_hint(np.array([40.0, 30, 6]), np.array([40.0, 30.5, 6])) is None


def test_an_open_mesh_is_refused_in_plain_words():
    t = box(20, 20, 20)[:-4]    # two faces missing
    try:
        rm.compare(box(20, 20, 20), t)
    except rm.MatchError as exc:
        assert "closed solid" in str(exc)
    else:
        raise AssertionError("expected MatchError")


def test_empty_and_oversized_meshes_are_refused():
    for bad in (np.zeros((0, 3, 3)), np.zeros((rm.MAX_TRIANGLES + 1, 3, 3))):
        try:
            rm.compare(box(5, 5, 5), bad)
        except rm.MatchError:
            pass
        else:
            raise AssertionError("expected MatchError")


def test_the_sentence_reads_plainly():
    r = rm.compare(box(40, 30, 6), box(40, 30, 6))
    s = rm.sentence(r)
    assert s.startswith("Very close:") and "%" in s and "1.00 times" in s


def test_stl_upload_path_end_to_end():
    import meshes
    gen = meshes.write_stl(box(40, 30, 6))
    ref = meshes.write_stl(box(40, 30, 6, at=(5, 5, 5)))
    r = rm.match_upload("stl", ref, gen)
    assert r["match_percent"] > 97
    try:
        rm.match_upload("pdf", b"x", gen)
    except rm.MatchError:
        pass
    else:
        raise AssertionError("expected MatchError")


# ------------------------------------------------------------- chat service ---

import copy

from _chat_fakes import BRACKET, fresh_store

import chat_service as svc
import chat_store as cs
from conversation import Plan

U = "0xuser"


def _verts_tris(tris):
    return tris.reshape(-1, 3), np.arange(len(tris) * 3).reshape(-1, 3)


def _deps(mesh=None, with_match=True):
    def generate(desc, spec, user, source):
        return {"success": True, "job_id": "j1", "parameters": copy.deepcopy(spec), "validation": {"warnings": [], "corrections": {}}}

    def reference_match(kind, data, gen_stl):
        try:
            return rm.match_upload(kind, data, gen_stl)
        except rm.MatchError as exc:
            raise svc.ReferenceUnavailable(str(exc)) from exc

    return svc.Deps(plan=lambda h, s: Plan("build", "Built.", copy.deepcopy(BRACKET), "llm"), generate=generate,
                    balance=lambda u: 5, part_mesh=lambda job: mesh if mesh is not None else _verts_tris(box(40, 30, 6)),
                    reference_match=reference_match if with_match else None)


def _chat_with_part():
    fresh_store()
    cid = cs.create_chat(U)["id"]
    out = svc.run_turn(_deps(), U, cid, "a plate")
    return cid, out["part"]["id"]


def test_matching_a_reference_adds_a_free_message_with_the_score():
    import meshes
    cid, pid = _chat_with_part()
    ref = meshes.write_stl(box(40, 30, 6, at=(3, 3, 3)))
    out = svc.match_reference(_deps(), U, cid, pid, "ref.stl", "stl", ref)
    assert out["match"]["match_percent"] > 97 and out["charged"] == 0
    stored = cs.get_message(U, cid, out["assistant_message"]["id"])
    assert stored["meta"]["match"]["filename"] == "ref.stl" and "Very close" in stored["content"]


def test_a_reference_that_is_not_closed_is_refused_in_plain_words():
    import meshes
    cid, pid = _chat_with_part()
    ref = meshes.write_stl(box(40, 30, 6)[:-4])
    try:
        svc.match_reference(_deps(), U, cid, pid, "bad.stl", "stl", ref)
    except svc.ReferenceUnavailable as exc:
        assert "closed solid" in str(exc)
    else:
        raise AssertionError("expected ReferenceUnavailable")


def test_matching_needs_the_feature_a_real_part_and_the_owner():
    cid, pid = _chat_with_part()
    for args, exc in (((_deps(with_match=False), U, cid, pid), svc.ReferenceUnavailable),
                      ((_deps(), U, cid, "nope"), svc.PartNotFound),
                      ((_deps(), "0xother", cid, pid), svc.ChatNotFound)):
        try:
            svc.match_reference(*args, "ref.stl", "stl", b"x")
        except exc:
            pass
        else:
            raise AssertionError(f"expected {exc.__name__}")
