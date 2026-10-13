"""Phone Pairing v1: rezolvarea IP-ului clientului pentru rate limiting (funcții pure, fără PostgreSQL)."""
from __future__ import annotations

import logging

import pytest

import cimitir_phone_pairing_http as pph

T = pph.parse_trusted_proxies("10.0.0.5, 172.16.0.0/12")


@pytest.mark.parametrize("peer,xff,want", [
    ("198.51.100.7", ["1.2.3.4"], "198.51.100.7"),                       # peer neîncrezut: XFF ignorat
    ("198.51.100.7", ["1.2.3.4, 10.0.0.5"], "198.51.100.7"),
    ("10.0.0.5", ["203.0.113.9"], "203.0.113.9"),                        # proxy de încredere: clientul real
    ("10.0.0.5", ["9.9.9.9, 203.0.113.9"], "203.0.113.9"),               # stânga = controlat de client, nefolosit
    ("10.0.0.5", ["203.0.113.9, 172.16.4.4"], "203.0.113.9"),            # lanț de proxy-uri de încredere
    ("10.0.0.5", ["203.0.113.9", "172.16.4.4"], "203.0.113.9"),          # mai multe header-e
    ("10.0.0.5", [], "10.0.0.5"),                                        # fără header: IP-ul proxy-ului
    ("10.0.0.5", ["10.0.0.5, 172.16.0.1"], "10.0.0.5"),                  # doar proxy-uri de încredere
    ("10.0.0.5", ["not-an-ip"], "10.0.0.5"),                             # malformat: fail-safe
    ("10.0.0.5", ["203.0.113.9:5555"], "10.0.0.5"),
    ("10.0.0.5", ["203.0.113.9,,1.1.1.1"], "10.0.0.5"),
    ("10.0.0.5", ["1.1.1.1," * 25 + "2.2.2.2"], "10.0.0.5"),            # prea multe intrări
    ("10.0.0.5", ["1" * 2000], "10.0.0.5"),                              # prea lung
    ("::ffff:10.0.0.5", ["203.0.113.9"], "203.0.113.9"),                 # IPv4 mapat în IPv6
    ("10.0.0.5", ["2001:db8:1:2:aaaa:bbbb:cccc:dddd"], "2001:db8:1:2::/64"),   # IPv6: găleată /64
    ("2001:db8:1:2:1::1", [], "2001:db8:1:2::/64"),
    ("2001:db8:1:2:9::9", [], "2001:db8:1:2::/64"),                      # două adrese din același /64 = aceeași găleată
    ("testclient", ["1.2.3.4"], "testclient"),                           # peer nonstandard: neschimbat
    ("", [], "unknown"),
])
def test_resolve_client_ip(peer, xff, want):
    assert pph.resolve_client_ip(peer, xff, T) == want


def test_no_trusted_config_ignores_forwarded_for_always():
    for xff in (["1.2.3.4"], ["1.2.3.4, 10.0.0.5"], []):
        assert pph.resolve_client_ip("10.0.0.5", xff, ()) == "10.0.0.5"


@pytest.mark.parametrize("raw", ["", "  ", "10.0.0.5", "10.0.0.5,172.16.0.0/12", " 10.0.0.5 , ::1 ", "2001:db8::/32"])
def test_parse_valid(raw):
    assert isinstance(pph.parse_trusted_proxies(raw), tuple)


@pytest.mark.parametrize("raw", ["garbage", "1", "2", "0.0.0.0/0", "::/0", "10.0.0.5,,", ",", "10.0.0.5,x", "10.0.0.5/8", "*", "10.0.0.5 10.0.0.6", "10.0.0.5;10.0.0.6"])
def test_parse_invalid_raises(raw):
    with pytest.raises(ValueError):
        pph.parse_trusted_proxies(raw)


def test_invalid_env_is_fail_safe_and_warns_once(monkeypatch, caplog):
    monkeypatch.setattr(pph, "_warned_invalid", False)
    monkeypatch.setenv(pph.ENV_TRUSTED, "0.0.0.0/0")
    caplog.set_level(logging.WARNING)
    assert pph.trusted_proxies() == () and pph.trusted_proxies() == ()
    assert [r.getMessage() for r in caplog.records].count(f"{pph.ENV_TRUSTED} este invalid: header-ele forward sunt ignorate, rate limiting folosește IP-ul conexiunii") == 1
    assert pph.resolve_client_ip("10.0.0.5", ["203.0.113.9"], pph.trusted_proxies()) == "10.0.0.5"
    monkeypatch.delenv(pph.ENV_TRUSTED)
    assert pph.trusted_proxies() == ()
