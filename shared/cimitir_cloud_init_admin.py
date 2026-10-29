"""cimitir_cloud_init_admin.py — MAIN inițializează primul administrator Cloud fără BOOTSTRAP_TOKEN manual și fără recrearea containerului.

Autorizarea = cheia privată „proprietar instanță” (Ed25519, în SecretStore), NU licența. MAIN semnează un grant scurt legat de instanță (vezi `cimitir_instance_grant`);
serverul îl verifică și îl acceptă doar cât timp nu există administrator. Parola administratorului nu se păstrează; grantul/cheia nu apar în rezultate sau loguri.
Prerequisit unic, la provisionarea instanței: `CIMITIR_INSTANCE_ID` + `CIMITIR_INSTANCE_ADMIN_PUBKEY` (cheia PUBLICĂ afișată de `create_owner_key`).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import cimitir_cloud_admin_client as adc
import cimitir_instance_grant as ig


class InitError(Exception):
    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


class InitAdmin:
    def __init__(self, url: str, store, client: Optional[adc.AdminClient] = None):
        import cimitir_cloud_associate as assoc
        self.url, self.store = assoc.normalize_url(url), store
        self._client = client

    def _cl(self) -> adc.AdminClient:
        return self._client or adc.AdminClient(self.url)

    def status(self) -> Dict[str, Any]:
        cl = self._cl()
        try:
            st = cl.status()
        except adc.AdminApiError as exc:
            raise InitError(str(exc), "api") from None
        finally:
            if self._client is None:
                cl.close()
        key = self.store.get(ig.secret_name(st["instance_id"])) if st.get("instance_id") else None
        mine = ig.fingerprint(ig.public_from_private(key)) if key else None
        return {"initialized": st["initialized"], "instance_id": st.get("instance_id"), "grant_supported": "signed_grant" in st.get("bootstrap_methods", []),
                "owner_key_present": bool(key), "owner_key_matches_server": bool(key) and mine == st.get("owner_key_fingerprint"),
                "legacy_token_configured": bool(st.get("legacy_token_configured")), "bootstrap_open": bool(st.get("bootstrap_open"))}

    def create_owner_key(self, instance_id: str) -> Dict[str, str]:
        """Generează cheia proprietarului instanței (o singură dată). Întoarce DOAR cheia publică, de pus în CIMITIR_INSTANCE_ADMIN_PUBKEY la provisionarea instanței."""
        name = ig.secret_name(instance_id)
        if self.store.get(name):
            raise InitError("Există deja o cheie de proprietar pentru această instanță; nu se suprascrie.", "exists")
        kp = ig.generate_keypair()
        self.store.set(name, kp["private_key"])
        return {"instance_id": instance_id, "public_key": kp["public_key"], "fingerprint": ig.fingerprint(kp["public_key"])}

    def initialize(self, email: str, password: str, confirmed: bool, organization_uid: Optional[str] = None, organization_name: str = "",
                   cemetery_uid: Optional[str] = None, cemetery_name: str = "") -> Dict[str, Any]:
        if confirmed is not True:
            raise InitError("Inițializarea administratorului necesită confirmarea explicită.", "confirm")
        st = self.status()
        if st["initialized"]:
            raise InitError("Instanța Cloud are deja un administrator; inițializarea nu mai este permisă.", "initialized")
        if not st["grant_supported"]:
            raise InitError("Instanța nu acceptă autorizarea prin grant semnat (lipsesc CIMITIR_INSTANCE_ID / CIMITIR_INSTANCE_ADMIN_PUBKEY la provisionare).", "unsupported")
        if not st["owner_key_present"]:
            raise InitError("Pe acest calculator lipsește cheia proprietarului instanței; fără ea administratorul nu poate fi inițializat.", "no_key")
        if not st["owner_key_matches_server"]:
            raise InitError("Cheia proprietarului din acest calculator nu corespunde instanței Cloud (altă instanță sau alt proprietar). Nimic nu a fost trimis.", "key_mismatch")
        grant = ig.issue_grant(self.store.get(ig.secret_name(st["instance_id"])), st["instance_id"], ttl=300)
        cl = self._cl()
        try:
            res = cl.bootstrap(grant, email, password, "", organization_uid, organization_name, cemetery_uid, cemetery_name)
        except adc.AdminApiError as exc:
            raise InitError(str(exc), "rejected") from None
        finally:
            del grant
            if self._client is None:
                cl.close()
        after = self.status()
        return {"initialized": after["initialized"], "organization_uid": res.get("organization_uid"), "cemetery_uid": res.get("cemetery_uid"), "email": res.get("email"),
                "legacy_token_configured": after["legacy_token_configured"],
                "message": "Administratorul Cloud a fost inițializat. Autorizarea prin grant nu mai poate fi folosită pe această instanță."
                           + (" Elimină BOOTSTRAP_TOKEN din configurația containerului (nu mai este necesar)." if after["legacy_token_configured"] else "")}
