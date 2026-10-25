"""chat_store: ownership, ordering, limits, import safety."""
from _chat_fakes import BRACKET, fresh_store

import chat_store as cs

A, B = "0xaaa", "0xbbb"


def test_create_list_and_rename():
    fresh_store()
    c = cs.create_chat(A, "  My   bracket ")
    assert c["title"] == "My bracket"
    assert [x["id"] for x in cs.list_chats(A)] == [c["id"]]
    assert cs.list_chats(B) == []
    assert cs.rename_chat(A, c["id"], "Renamed")
    assert cs.get_chat(A, c["id"])["title"] == "Renamed"


def test_other_accounts_cannot_touch_a_chat():
    fresh_store()
    c = cs.create_chat(A)
    assert cs.get_chat(B, c["id"]) is None
    assert not cs.rename_chat(B, c["id"], "x")
    assert not cs.delete_chat(B, c["id"])
    for fn in (lambda: cs.add_message(B, c["id"], "user", "hi"),
               lambda: cs.latest_part(B, c["id"]),
               lambda: cs.recent_messages(B, c["id"], 5),
               lambda: cs.add_part(B, c["id"], job_id="j", title="t", part_type="p", spec={})):
        try:
            fn()
            raise AssertionError("expected LookupError")
        except LookupError:
            pass
    assert cs.get_chat(A, c["id"])["title"] == "New design"


def test_messages_keep_order_and_recent_window():
    fresh_store()
    c = cs.create_chat(A)
    for i in range(6):
        cs.add_message(A, c["id"], "user" if i % 2 == 0 else "assistant", f"m{i}")
    got = cs.recent_messages(A, c["id"], 4)
    assert [m["content"] for m in got] == ["m2", "m3", "m4", "m5"]
    assert [m["seq"] for m in cs.get_chat(A, c["id"])["messages"]] == [1, 2, 3, 4, 5, 6]


def test_part_versions_increment_and_latest_wins():
    fresh_store()
    c = cs.create_chat(A)
    p1 = cs.add_part(A, c["id"], job_id="j1", title="L bracket", part_type="l_bracket", spec=BRACKET)
    spec2 = {**BRACKET, "parameters": {**BRACKET["parameters"], "thickness_mm": 4}}
    p2 = cs.add_part(A, c["id"], job_id="j2", title="L bracket", part_type="l_bracket", spec=spec2, notes=["n"])
    assert (p1["version"], p2["version"]) == (1, 2)
    latest = cs.latest_part(A, c["id"])
    assert latest["job_id"] == "j2" and latest["spec"]["parameters"]["thickness_mm"] == 4 and latest["notes"] == ["n"]


def test_delete_hides_chat():
    fresh_store()
    c = cs.create_chat(A)
    assert cs.delete_chat(A, c["id"])
    assert cs.list_chats(A) == [] and cs.get_chat(A, c["id"]) is None
    assert not cs.delete_chat(A, c["id"])


def test_limits():
    fresh_store()
    cs.create_chat(A, max_chats=1)
    try:
        cs.create_chat(A, max_chats=1)
        raise AssertionError("expected ChatLimitError")
    except cs.ChatLimitError:
        pass
    c = cs.create_chat(B)
    cs.add_message(B, c["id"], "user", "1", max_messages=1)
    try:
        cs.add_message(B, c["id"], "user", "2", max_messages=1)
        raise AssertionError("expected ChatLimitError")
    except cs.ChatLimitError:
        pass


def test_set_title_if_default_only_once():
    fresh_store()
    c = cs.create_chat(A)
    cs.set_title_if_default(A, c["id"], "First")
    cs.set_title_if_default(A, c["id"], "Second")
    assert cs.get_chat(A, c["id"])["title"] == "First"


def test_import_keeps_only_owned_jobs():
    fresh_store()
    local = {
        "title": "Old chat",
        "parts": {"p1": {"jobId": "mine", "title": "Gear", "partType": "gear", "params": {"teeth": 20}},
                  "p2": {"jobId": "theirs", "title": "Stolen", "partType": "gear", "params": {}}},
        "messages": [{"role": "user", "text": "gear"}, {"role": "assistant", "text": "ok", "partId": "p1"},
                     {"role": "assistant", "text": "also", "partId": "p2"}],
    }
    cid = cs.import_chat(A, local, owns_job=lambda j: j == "mine")
    chat = cs.get_chat(A, cid)
    assert [p["job_id"] for p in chat["parts"]] == ["mine"]
    by = {m["content"]: m["part_id"] for m in chat["messages"]}
    assert by["ok"] == chat["parts"][0]["id"] and by["also"] is None
