from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.utils.client_ip import _coerce_ip, get_client_ip


class _RequestMock:
    def __init__(
        self,
        *,
        headers: object | None = None,
        client_host: str | None = "203.0.113.10",
    ) -> None:
        self._headers = {} if headers is None else headers
        self._client = (
            SimpleNamespace(host=client_host) if client_host is not None else None
        )

    @property
    def headers(self):
        return self._headers

    @property
    def client(self):
        return self._client


class _ExplodingHeaders(dict[str, str]):
    def get(self, key, default=None):
        raise AssertionError(f"unexpected header access: {key}")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("198.51.100.24", "198.51.100.24"),
        ("2001:db8::1", "2001:db8::1"),
        ("  203.0.113.7  ", "203.0.113.7"),
    ],
)
def test_coerce_ip_returns_normalized_valid_addresses(
    value: str | None,
    expected: str,
) -> None:
    assert _coerce_ip(value) == expected


@pytest.mark.parametrize("value", [None, "", "   ", "not-an-ip"])
def test_coerce_ip_returns_none_for_missing_or_invalid_values(
    value: str | None,
) -> None:
    assert _coerce_ip(value) is None


def test_get_client_ip_returns_request_client_host_when_proxy_headers_are_untrusted() -> (
    None
):
    request = _RequestMock(
        headers={"cf-connecting-ip": "198.51.100.8"},
        client_host="203.0.113.10",
    )

    assert get_client_ip(request, trust_x_forwarded_for=False) == "203.0.113.10"


def test_get_client_ip_returns_none_without_client_when_proxy_headers_are_untrusted() -> (
    None
):
    request = _RequestMock(headers={"x-real-ip": "198.51.100.8"}, client_host=None)

    assert get_client_ip(request, trust_x_forwarded_for=False) is None


def test_get_client_ip_does_not_read_headers_when_proxy_headers_are_untrusted() -> None:
    request = _RequestMock(
        headers=_ExplodingHeaders(cf_connecting_ip="198.51.100.8"),
        client_host="203.0.113.10",
    )

    assert get_client_ip(request, trust_x_forwarded_for=False) == "203.0.113.10"


def test_get_client_ip_prefers_cf_connecting_ip_when_proxy_headers_are_trusted() -> (
    None
):
    request = _RequestMock(
        headers={
            "cf-connecting-ip": "198.51.100.8",
            "x-forwarded-for": "203.0.113.20, 203.0.113.21",
            "x-real-ip": "203.0.113.30",
        },
        client_host="203.0.113.40",
    )

    assert get_client_ip(request, trust_x_forwarded_for=True) == "198.51.100.8"


def test_get_client_ip_uses_x_forwarded_for_when_cf_connecting_ip_is_absent() -> None:
    request = _RequestMock(
        headers={"x-forwarded-for": "198.51.100.8"},
        client_host="203.0.113.10",
    )

    assert get_client_ip(request, trust_x_forwarded_for=True) == "198.51.100.8"


def test_get_client_ip_uses_leftmost_x_forwarded_for_address_when_multiple_are_present() -> (
    None
):
    request = _RequestMock(
        headers={"x-forwarded-for": " 198.51.100.8, 203.0.113.20 "},
        client_host="203.0.113.10",
    )

    assert get_client_ip(request, trust_x_forwarded_for=True) == "198.51.100.8"


def test_get_client_ip_skips_invalid_x_forwarded_for_entries_until_a_valid_ip_is_found() -> (
    None
):
    request = _RequestMock(
        headers={"x-forwarded-for": "not-an-ip, 2001:db8::25, 203.0.113.20"},
        client_host="203.0.113.10",
    )

    assert get_client_ip(request, trust_x_forwarded_for=True) == "2001:db8::25"


def test_get_client_ip_uses_x_real_ip_when_cf_and_x_forwarded_for_are_absent() -> None:
    request = _RequestMock(
        headers={"x-real-ip": "198.51.100.8"},
        client_host="203.0.113.10",
    )

    assert get_client_ip(request, trust_x_forwarded_for=True) == "198.51.100.8"


def test_get_client_ip_falls_back_to_direct_client_host_when_no_proxy_headers_are_present() -> (
    None
):
    request = _RequestMock(headers={}, client_host="203.0.113.10")

    assert get_client_ip(request, trust_x_forwarded_for=True) == "203.0.113.10"


def test_get_client_ip_ignores_invalid_cf_connecting_ip_and_falls_through_to_x_forwarded_for() -> (
    None
):
    request = _RequestMock(
        headers={
            "cf-connecting-ip": "not-an-ip",
            "x-forwarded-for": "198.51.100.8, 203.0.113.20",
        },
        client_host="203.0.113.10",
    )

    assert get_client_ip(request, trust_x_forwarded_for=True) == "198.51.100.8"


def test_get_client_ip_ignores_empty_x_forwarded_for_and_falls_through_to_x_real_ip() -> (
    None
):
    request = _RequestMock(
        headers={"x-forwarded-for": "", "x-real-ip": "198.51.100.8"},
        client_host="203.0.113.10",
    )

    assert get_client_ip(request, trust_x_forwarded_for=True) == "198.51.100.8"
