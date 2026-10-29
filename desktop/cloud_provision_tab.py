"""cloud_provision_tab.py — „G. Provizionare” în Centrul Cloud: instanțele parohiilor create prin serviciul de provizionare al operatorului.

Tokenul de operator se păstrează în SecretStore (DPAPI), NU în SQLite/loguri. Parola administrativă a bazei Supabase se cere per acțiune, nu se salvează și se șterge din câmp
imediat după apel. Cheia privată a proprietarului instanței (returnată o singură dată la înregistrare) merge direct în SecretStore, în locul folosit de fila „Administratori”.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

import cimitir_instance_grant as ig
import cimitir_prov_client as pc
import field_inbox_client as fic
from field_worker import run_task

TOKEN_SECRET = "provisioner/operator-token"
URL_SECRET = "provisioner/url"


def _wrap(fn):
    def w():
        try:
            return fn()
        except pc.ProvisionerClientError as exc:
            raise fic.FieldCloudError(f"{exc} [{exc.code}]") from None
    return w


class ProvisionTab(QWidget):
    def __init__(self, store: Any, can: Callable[[], bool], parent=None, client_factory: Optional[Callable[[str, str], Any]] = None):
        super().__init__(parent)
        self._store, self._can = store, can
        self._factory = client_factory or (lambda url, tok: pc.ProvisionerClient(url, tok))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Instanțe Cloud ale parohiilor (serviciul de provizionare controlat de operator). Licența .lic NU autorizează crearea de instanțe."))
        form = QFormLayout()
        self.ed_url, self.ed_token = QLineEdit(), QLineEdit()
        self.ed_url.setObjectName("prov_url")
        self.ed_token.setObjectName("prov_token")
        self.ed_token.setEchoMode(QLineEdit.Password)
        self.ed_token.setPlaceholderText("token operator (se salvează în SecretStore)")
        form.addRow("Adresa serviciului de provizionare (https://…)", self.ed_url)
        form.addRow("Token operator", self.ed_token)
        lay.addLayout(form)
        row = QHBoxLayout()
        self.btn_save, self.btn_refresh, self.btn_health = QPushButton("Salvează conexiunea"), QPushButton("Reîmprospătează"), QPushButton("Verifică sănătatea instanței selectate")
        for b, n in ((self.btn_save, "prov_save"), (self.btn_refresh, "prov_refresh"), (self.btn_health, "prov_health")):
            b.setObjectName(n)
            row.addWidget(b)
        lay.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("prov_table")
        self.table.setHorizontalHeaderLabels(["Parohie", "Domeniu", "Stare", "Release", "Mediu"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self.table, 1)
        nf = QFormLayout()
        self.ed_slug, self.ed_parish, self.ed_domain, self.ed_admin = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.ed_admin.setEchoMode(QLineEdit.Password)
        self.ed_admin.setPlaceholderText("postgresql://postgres:…@db.<ref>.supabase.co:5432/postgres (nu se salvează)")
        for e, n in ((self.ed_slug, "prov_slug"), (self.ed_parish, "prov_parish"), (self.ed_domain, "prov_domain"), (self.ed_admin, "prov_admin_url")):
            e.setObjectName(n)
        nf.addRow("Identificator parohie (slug)", self.ed_slug)
        nf.addRow("Denumire parohie", self.ed_parish)
        nf.addRow("Domeniu/subdomeniu", self.ed_domain)
        nf.addRow("URL administrativ Supabase (doar pentru acest pas)", self.ed_admin)
        lay.addLayout(nf)
        self.btn_new = QPushButton("Înregistrează instanță nouă (generează cheia proprietarului)")
        self.btn_new.setObjectName("prov_new")
        lay.addWidget(self.btn_new)
        self.txt = QPlainTextEdit()
        self.txt.setObjectName("prov_out")
        self.txt.setReadOnly(True)
        lay.addWidget(self.txt)
        self.btn_save.clicked.connect(self.save_connection)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_health.clicked.connect(self.check_health)
        self.btn_new.clicked.connect(self.register_new)
        self._load()

    def _load(self) -> None:
        try:
            self.ed_url.setText(self._store.get(URL_SECRET) or "")
        except Exception:  # noqa: BLE001
            pass

    def _client(self):
        url, tok = self.ed_url.text().strip() or (self._store.get(URL_SECRET) or ""), self.ed_token.text().strip() or (self._store.get(TOKEN_SECRET) or "")
        if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
            raise fic.FieldCloudError("Adresa serviciului de provizionare trebuie să fie HTTPS (sau 127.0.0.1 pentru demonstrație).")
        if not tok:
            raise fic.FieldCloudError("Lipsește tokenul de operator. Introdu-l și apasă „Salvează conexiunea”.")
        return self._factory(url, tok)

    def _bg(self, fn: Callable[[Any], Any], ok: Callable[[Any], None]) -> None:
        if not self._can():
            return
        try:
            c = self._client()
        except fic.FieldCloudError as exc:
            self.txt.setPlainText(str(exc))
            return
        run_task(self, _wrap(lambda: fn(c)), ok, lambda m, _nc: self.txt.setPlainText(m))

    def save_connection(self) -> None:
        if not self._can():
            return
        if self.ed_token.text().strip():
            self._store.set(TOKEN_SECRET, self.ed_token.text().strip())
            self.ed_token.clear()
        if self.ed_url.text().strip():
            self._store.set(URL_SECRET, self.ed_url.text().strip())
        self.txt.setPlainText("Conexiune salvată (tokenul este în SecretStore, nu în baza de date).")

    def refresh(self) -> None:
        def ok(items: list) -> None:
            self.items = items
            self.table.setRowCount(len(items))
            for r, i in enumerate(items):
                for c, v in enumerate((i["parish_name"], i["domain"], pc.STATUS_RO.get(i["status"], i["status"]), i.get("release") or "—", i["environment"])):
                    self.table.setItem(r, c, QTableWidgetItem(str(v)))
        self._bg(lambda c: c.list_instances(), ok)

    def _selected(self) -> Optional[str]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return getattr(self, "items", [{}])[rows[0].row()].get("slug") if rows else None

    def check_health(self) -> None:
        slug = self._selected()
        if not slug:
            self.txt.setPlainText("Selectează o instanță din listă.")
            return
        self._bg(lambda c: (c.health(slug), c.get(slug)), lambda r: self.txt.setPlainText("\n".join(pc.status_lines(r[1]))))

    def register_new(self) -> None:
        slug, parish, domain = self.ed_slug.text().strip(), self.ed_parish.text().strip(), self.ed_domain.text().strip()

        def ok(r: dict) -> None:
            self._store.set(ig.secret_name(r["slug"]), r["owner_private_key_ONCE"])         # cheia privată: doar în SecretStore
            self.txt.setPlainText(f"Instanță înregistrată: {r['slug']}. Cheia proprietarului a fost salvată în SecretStore (amprentă {r['owner_fingerprint']}).\n"
                                  "Pasul următor: asociază proiectul Supabase al parohiei și rulează provizionarea; apoi „D. Administratori” → Inițializare administrator.")
            self.refresh()
        self._bg(lambda c: c.register(slug, parish, domain, generate_owner_key=True), ok)
