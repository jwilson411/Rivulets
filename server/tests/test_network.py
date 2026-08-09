from rivulets.security.network import detect_lan_address, is_loopback_host


def test_is_loopback_host_recognizes_common_loopback_forms() -> None:
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("localhost")
    assert is_loopback_host("LOCALHOST")
    assert is_loopback_host("::1")


def test_is_loopback_host_rejects_non_loopback_hosts() -> None:
    assert not is_loopback_host("192.168.1.5")
    assert not is_loopback_host("example.com")
    assert not is_loopback_host("")


def test_detect_lan_address_returns_a_string_or_none_and_never_raises() -> None:
    # Best-effort: on a machine with no network route this legitimately
    # returns None, so only assert on the type, not a specific value.
    result = detect_lan_address()
    assert result is None or isinstance(result, str)
