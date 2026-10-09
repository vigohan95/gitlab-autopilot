from pulse.store import Store


def test_add_and_list_sites():
    s = Store()
    a = s.add_site("a", "https://a.test/")
    b = s.add_site("b", "https://b.test/")
    assert [x.name for x in s.list_sites()] == ["a", "b"]
    assert s.get_site(a.id) == a
    assert s.get_site_by_name("b") == b
    assert s.get_site(999) is None


def test_delete_site_cascades_checks():
    s = Store()
    a = s.add_site("a", "https://a.test/")
    s.record_check(a.id, True, 200, 12.5)
    assert s.delete_site(a.id) is True
    assert s.delete_site(a.id) is False
    assert s.recent_checks() == []


def test_record_and_recent_checks_ordering():
    s = Store()
    a = s.add_site("a", "https://a.test/")
    s.record_check(a.id, True, 200, 10.0, ts="2026-01-01T00:00:00+00:00")
    s.record_check(a.id, False, 500, 20.0, error="HTTP 500", ts="2026-01-01T00:01:00+00:00")
    recent = s.recent_checks(a.id)
    assert [c.ok for c in recent] == [False, True]
    assert recent[0].error == "HTTP 500"
    assert len(s.recent_checks(a.id, limit=1)) == 1


def test_summary_uptime_and_latency():
    s = Store()
    a = s.add_site("a", "https://a.test/")
    s.add_site("empty", "https://e.test/")
    for ok, lat in [(True, 10.0), (True, 30.0), (False, None), (True, 20.0)]:
        s.record_check(a.id, ok, 200 if ok else None, lat, None if ok else "ConnectError")
    rows = {r["name"]: r for r in s.summary()}
    assert rows["a"]["checks"] == 4
    assert rows["a"]["uptime_pct"] == 75.0
    assert rows["a"]["mean_latency_ms"] == 20.0
    assert rows["a"]["last_ok"] is True
    assert rows["empty"]["checks"] == 0
    assert rows["empty"]["uptime_pct"] is None


def test_seed_is_idempotent():
    s = Store()
    s.seed([("a", "https://a.test/")])
    s.seed([("a", "https://a.test/"), ("b", "https://b.test/")])
    assert [x.name for x in s.list_sites()] == ["a", "b"]
