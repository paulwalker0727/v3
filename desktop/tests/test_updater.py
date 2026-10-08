"""Teste pentru mecanismul de update GitHub Releases (iteration 4).

Aplicație DESKTOP PySide6, teste headless.  ZERO cereri reale către GitHub;
`requests.get` este mockuit în toate testele.
"""
from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---- Qt bootstrap ---------------------------------------------------------
@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def updater_mod():
    # Reload de fiecare dată — testele care setează CIMITIR_UPDATE_REPO
    # invalidează modulul cache-uit.
    if "updater" in sys.modules:
        import updater as _u
        importlib.reload(_u)
    else:
        import updater as _u  # noqa: F401
    import updater
    updater.reset_update_check()
    return updater


# =========================================================================
# 1. parse_semver — cazuri limită
# =========================================================================
class TestParseSemver:
    def test_accepts_v_prefix(self, updater_mod):
        assert updater_mod.parse_semver("v1.2.3")[:3] == (1, 2, 3)

    def test_accepts_no_prefix(self, updater_mod):
        assert updater_mod.parse_semver("1.2.3")[:3] == (1, 2, 3)

    def test_accepts_two_component(self, updater_mod):
        assert updater_mod.parse_semver("1.2")[:3] == (1, 2, 0)

    def test_accepts_prerelease(self, updater_mod):
        v = updater_mod.parse_semver("1.2.3-beta")
        assert v[:3] == (1, 2, 3)
        # marker prerelease=0 vs stable=1
        assert v[3] == 0
        assert v[4] == "beta"

    @pytest.mark.parametrize("bad", ["abc", "", "v", "1.a", None])
    def test_invalid_raises(self, updater_mod, bad):
        with pytest.raises(ValueError):
            updater_mod.parse_semver(bad)

    def test_ordering_numeric_not_lexicographic(self, updater_mod):
        assert updater_mod.parse_semver("v1.10.0") > updater_mod.parse_semver("1.9.0")

    def test_equal_regardless_of_v_prefix(self, updater_mod):
        assert updater_mod.parse_semver("1.1.0") == updater_mod.parse_semver("v1.1.0")

    def test_prerelease_less_than_stable(self, updater_mod):
        assert updater_mod.parse_semver("1.1.0-beta") < updater_mod.parse_semver("1.1.0")

    def test_major_bump(self, updater_mod):
        assert updater_mod.parse_semver("2.0.0") > updater_mod.parse_semver("1.99.99")


# =========================================================================
# 2. get_latest_release — degradare silențioasă
# =========================================================================
class _FakeResponse:
    def __init__(self, status_code=200, payload=None, raise_json=False):
        self.status_code = status_code
        self._payload = payload
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("invalid json")
        return self._payload


class TestGetLatestRelease:
    def test_returns_release_info_on_200(self, updater_mod, monkeypatch):
        payload = {
            "tag_name": "v1.5.0",
            "html_url": "https://example.com/rel/v1.5.0",
            "assets": [
                {"name": "installer.exe",
                 "browser_download_url": "https://example.com/installer.exe"},
                {"name": "no-url"},  # trebuie filtrat
                {"name": "src.zip",
                 "browser_download_url": "https://example.com/src.zip"},
            ],
        }
        monkeypatch.setattr(updater_mod.requests, "get",
                            lambda *a, **k: _FakeResponse(200, payload))
        rel = updater_mod.get_latest_release()
        assert rel is not None
        assert rel.tag_name == "v1.5.0"
        assert rel.html_url == "https://example.com/rel/v1.5.0"
        assert rel.version[:3] == (1, 5, 0)
        assert len(rel.assets) == 2
        assert {a["name"] for a in rel.assets} == {"installer.exe", "src.zip"}

    @pytest.mark.parametrize("code", [404, 403, 429, 500, 502])
    def test_non_200_returns_none(self, updater_mod, monkeypatch, code):
        monkeypatch.setattr(updater_mod.requests, "get",
                            lambda *a, **k: _FakeResponse(code, {}))
        assert updater_mod.get_latest_release() is None

    def test_invalid_json_returns_none(self, updater_mod, monkeypatch):
        monkeypatch.setattr(updater_mod.requests, "get",
                            lambda *a, **k: _FakeResponse(200, None, raise_json=True))
        assert updater_mod.get_latest_release() is None

    def test_missing_tag_name_returns_none(self, updater_mod, monkeypatch):
        monkeypatch.setattr(updater_mod.requests, "get",
                            lambda *a, **k: _FakeResponse(200, {"html_url": "x"}))
        assert updater_mod.get_latest_release() is None

    def test_invalid_semver_tag_returns_none(self, updater_mod, monkeypatch):
        monkeypatch.setattr(updater_mod.requests, "get",
                            lambda *a, **k: _FakeResponse(200, {"tag_name": "banana"}))
        assert updater_mod.get_latest_release() is None

    def test_timeout_returns_none(self, updater_mod, monkeypatch):
        import requests as _r

        def raise_timeout(*a, **k):
            raise _r.Timeout("read timed out")

        monkeypatch.setattr(updater_mod.requests, "get", raise_timeout)
        assert updater_mod.get_latest_release() is None

    def test_connection_error_returns_none(self, updater_mod, monkeypatch):
        import requests as _r

        def raise_conn(*a, **k):
            raise _r.ConnectionError("no net")

        monkeypatch.setattr(updater_mod.requests, "get", raise_conn)
        assert updater_mod.get_latest_release() is None

    def test_empty_repo_env_returns_none(self, monkeypatch):
        monkeypatch.setenv("CIMITIR_UPDATE_REPO", "invalid-no-slash")
        # sys.modules poate avea `updater` cached; reload cu importlib
        if "updater" in sys.modules:
            del sys.modules["updater"]
        import updater as u
        u.reset_update_check()
        assert u.get_latest_release() is None


# =========================================================================
# 3. Headers, URL și env var
# =========================================================================
class TestRequestKwargs:
    def test_headers_and_timeout_sent(self, updater_mod, monkeypatch):
        captured = {}

        def spy(url, headers=None, timeout=None, **kw):
            captured["url"] = url
            captured["headers"] = headers
            captured["timeout"] = timeout
            return _FakeResponse(200, {"tag_name": "1.0.0", "html_url": "x", "assets": []})

        monkeypatch.setattr(updater_mod.requests, "get", spy)
        updater_mod.get_latest_release()

        assert captured["url"].startswith("https://api.github.com/repos/")
        assert captured["url"].endswith("/releases/latest")
        h = captured["headers"]
        assert "User-Agent" in h and h["User-Agent"]
        assert h.get("Accept") == "application/vnd.github+json"
        assert "X-GitHub-Api-Version" in h
        assert captured["timeout"] is not None

    def test_env_var_repo_is_honored_after_reimport(self, monkeypatch):
        monkeypatch.setenv("CIMITIR_UPDATE_REPO", "test-owner/test-repo")
        if "updater" in sys.modules:
            del sys.modules["updater"]
        import updater as u  # noqa: WPS433
        assert u.GITHUB_REPO == "test-owner/test-repo"
        assert u.RELEASE_API_URL == (
            "https://api.github.com/repos/test-owner/test-repo/releases/latest"
        )
        u.reset_update_check()


# =========================================================================
# 4. UpdateChecker — thread + semnale
# =========================================================================
def _process_until(qapp, checker, timeout_s: float = 5.0) -> bool:
    done = []
    checker.check_finished.connect(lambda: done.append(1))
    deadline = time.time() + timeout_s
    while time.time() < deadline and not done:
        qapp.processEvents()
        time.sleep(0.02)
    # asigură cleanup thread
    if hasattr(checker, "_thread"):
        checker._thread.wait(2000)
    return bool(done)


class TestUpdateChecker:
    def test_emits_when_remote_newer(self, updater_mod, qapp, monkeypatch):
        rel = updater_mod.ReleaseInfo(
            tag_name="v1.5.0",
            version=updater_mod.parse_semver("1.5.0"),
            html_url="https://example.com/rel",
            assets=[],
        )
        monkeypatch.setattr(updater_mod, "get_latest_release", lambda: rel)

        ck = updater_mod.UpdateChecker(version="1.0.0")
        events = []
        ck.update_available.connect(lambda tag, url: events.append((tag, url)))
        ck.start()
        assert _process_until(qapp, ck)
        assert events == [("v1.5.0", "https://example.com/rel")]

    def test_no_emit_when_remote_older(self, updater_mod, qapp, monkeypatch):
        rel = updater_mod.ReleaseInfo(
            tag_name="v0.9.0",
            version=updater_mod.parse_semver("0.9.0"),
            html_url="https://example.com/rel",
            assets=[],
        )
        monkeypatch.setattr(updater_mod, "get_latest_release", lambda: rel)
        ck = updater_mod.UpdateChecker(version="1.0.0")
        events = []
        ck.update_available.connect(lambda *a: events.append(a))
        ck.start()
        assert _process_until(qapp, ck)
        assert events == []

    def test_no_emit_when_none(self, updater_mod, qapp, monkeypatch):
        monkeypatch.setattr(updater_mod, "get_latest_release", lambda: None)
        ck = updater_mod.UpdateChecker(version="1.0.0")
        events = []
        ck.update_available.connect(lambda *a: events.append(a))
        ck.start()
        assert _process_until(qapp, ck)
        assert events == []


# =========================================================================
# 5. attach_update_banner
# =========================================================================
class TestAttachUpdateBanner:
    def _make_window(self, qapp):
        from PySide6.QtWidgets import QMainWindow
        return QMainWindow()

    def test_banner_added_when_update_available(self, updater_mod, qapp, monkeypatch):
        updater_mod.reset_update_check()
        rel = updater_mod.ReleaseInfo(
            tag_name="v1.5.0",
            version=updater_mod.parse_semver("1.5.0"),
            html_url="https://example.com/rel/v1.5.0",
            assets=[],
        )
        monkeypatch.setattr(updater_mod, "get_latest_release", lambda: rel)

        win = self._make_window(qapp)
        ck = updater_mod.attach_update_banner(win, version="1.0.0")
        assert ck is not None
        assert _process_until(qapp, ck)

        from PySide6.QtWidgets import QLabel, QPushButton
        labels = [w for w in win.findChildren(QLabel)
                  if w.objectName() == "update-banner-label"]
        assert len(labels) == 1
        assert "v1.5.0" in labels[0].text()
        assert "disponibil" in labels[0].text()

        # butonul download deschide URL-ul
        opened = []
        from PySide6.QtGui import QDesktopServices
        monkeypatch.setattr(QDesktopServices, "openUrl",
                            lambda url: opened.append(url.toString()) or True)
        btns = {b.objectName(): b for b in win.findChildren(QPushButton)}
        assert "update-banner-download" in btns
        btns["update-banner-download"].click()
        assert opened == ["https://example.com/rel/v1.5.0"]

        # dismiss ascunde bara
        toolbars = [t for t in win.findChildren(updater_mod.UpdateBanner)]
        assert toolbars
        assert not toolbars[0].isHidden()
        btns["update-banner-dismiss"].click()
        assert toolbars[0].isHidden()
        win.close()

    def test_no_banner_when_no_update(self, updater_mod, qapp, monkeypatch):
        updater_mod.reset_update_check()
        monkeypatch.setattr(updater_mod, "get_latest_release", lambda: None)
        win = self._make_window(qapp)
        ck = updater_mod.attach_update_banner(win, version="1.0.0")
        assert _process_until(qapp, ck)
        from PySide6.QtWidgets import QLabel
        labels = [w for w in win.findChildren(QLabel)
                  if w.objectName() == "update-banner-label"]
        assert labels == []
        win.close()

    def test_only_one_check_per_session(self, updater_mod, qapp, monkeypatch):
        updater_mod.reset_update_check()
        monkeypatch.setattr(updater_mod, "get_latest_release", lambda: None)
        win = self._make_window(qapp)
        first = updater_mod.attach_update_banner(win, version="1.0.0")
        assert first is not None
        # al doilea apel: fără reset => None
        second = updater_mod.attach_update_banner(win, version="1.0.0")
        assert second is None
        # după reset => se poate din nou
        updater_mod.reset_update_check()
        third = updater_mod.attach_update_banner(win, version="1.0.0")
        assert third is not None
        for ck in (first, third):  # nu lăsa thread-uri Qt pornite la ieșire (core dump)
            _process_until(qapp, ck)
        win.close()

    def test_object_without_addtoolbar_returns_none(self, updater_mod, monkeypatch):
        updater_mod.reset_update_check()

        class NoToolBar:
            pass

        assert updater_mod.attach_update_banner(NoToolBar()) is None

    def test_main_py_wraps_attach_in_try(self):
        """main.py trebuie să prindă orice excepție din updater."""
        src = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        assert "attach_update_banner" in src
        # trebuie să existe try/except în jurul importului/apelării
        idx = src.index("attach_update_banner")
        # caută 'try' înainte și 'except' după în fereastră mică
        window = src[max(0, idx - 300): idx + 300]
        assert "try" in window and "except" in window, (
            "Apelul attach_update_banner nu pare protejat de try/except"
        )
