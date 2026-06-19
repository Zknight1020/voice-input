from voice_input.core.context_cache import ContextCache


def test_push_and_get_within_ttl() -> None:
    c = ContextCache()
    c.push("hello", "cursor", ttl_s=10, now=100.0)
    e = c.get(now=105.0)
    assert e is not None
    assert e.text == "hello"
    assert e.source == "cursor"
    assert e.ttl_s == 10
    assert e.pushed_at == 100.0


def test_get_expires_after_ttl() -> None:
    c = ContextCache()
    c.push("hello", "cursor", ttl_s=10, now=100.0)
    assert c.get(now=110.001) is None
    # 过期后 entry 也被清掉
    assert c.get(now=109.0) is None


def test_latest_wins() -> None:
    c = ContextCache()
    c.push("first", "cursor", ttl_s=60, now=100.0)
    c.push("second", "cursor", ttl_s=60, now=101.0)
    e = c.get(now=102.0)
    assert e is not None and e.text == "second"


def test_clear() -> None:
    c = ContextCache()
    c.push("hello", "cursor", ttl_s=10, now=100.0)
    c.clear()
    assert c.get(now=101.0) is None


def test_empty() -> None:
    c = ContextCache()
    assert c.get(now=100.0) is None


def test_get_not_consumed() -> None:
    c = ContextCache()
    c.push("hello", "cursor", ttl_s=10, now=100.0)
    e1 = c.get(now=101.0)
    e2 = c.get(now=102.0)
    assert e1 is not None and e2 is not None
    assert e1.text == e2.text
