from tools.setup_manager import redact


def test_registered_secret_masked_incl_urlencoded():
    redact.register("p@ss/w0rd!")
    out = redact.redact("login p@ss/w0rd! and p%40ss%2Fw0rd%21 done")
    assert "p@ss" not in out and "p%40ss" not in out and out.count("***") == 2


def test_patterns_mask_connection_strings_jwt_and_keys():
    s = "postgresql://postgres.abc:SuperSecret@host:5432/db sb_secret_ABCdef123 "+"eyJ"+"hbGciOiJIUzI1.eyJyb2xlIjoic2VydmljZQ.sig123  password=hunter22"
    out = redact.redact(s)
    for leaked in ("SuperSecret", "sb_secret_ABC", "eyJhbGci", "hunter22"):
        assert leaked not in out


def test_redact_obj_nested():
    redact.register("topsecretvalue")
    assert redact.redact_obj({"a": ["x topsecretvalue"], "b": {"c": "topsecretvalue"}}) == {"a": ["x ***"], "b": {"c": "***"}}


def test_short_values_not_registered():
    redact.register("ab")
    assert redact.redact("abab") == "abab"
