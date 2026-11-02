"""Modelul problemelor și catalogul verificărilor (Pasul B – diagnostic de integritate, DOAR CITIRE)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SEVERITIES = ("critical", "error", "warning", "info")
SEVERITY_RO = {"critical": "critică", "error": "eroare", "warning": "avertisment", "info": "informație"}
SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}

CATEGORIES = ("locuri", "sectoare", "concesionari", "contracte", "inhumati", "avize", "documente", "plati", "sincronizare", "financiar")
CATEGORY_RO = {"locuri": "Locuri de veci", "sectoare": "Sectoare", "concesionari": "Concesionari", "contracte": "Contracte",
               "inhumati": "Înhumați", "avize": "Avize", "documente": "Documente și fotografii", "plati": "Plăți",
               "sincronizare": "Sincronizare", "financiar": "Evidențe financiare"}

_CNP_RX = re.compile(r"(?<!\d)\d{13}(?!\d)")
_CI_RX = re.compile(r"\b[A-Za-z]{2}\s?\d{6}\b")


def redact(text: str) -> str:
    """Plasă de siguranță: niciun CNP sau serie CI nu ajunge într-un raport, chiar dacă o verificare ar greși."""
    return _CI_RX.sub("[CI-omis]", _CNP_RX.sub("[CNP-omis]", text))


def redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(k): redact_obj(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_obj(v) for v in value]
    return value


# cod -> (severitate implicită, categorie, titlu, recomandare, remediabil automat într-o etapă viitoare?)
CATALOG: Dict[str, tuple] = {
    # ---- locuri
    "LOC001": ("critical", "locuri", "Loc de veci fără identificator (cod_loc)", "Atribuiți manual un cod_loc unic; fără el locul nu poate fi referit de contracte, plăți, avize sau documente.", False),
    "LOC002": ("warning", "locuri", "cod_loc cu spații la capete sau caractere de control", "Curățați manual codul; este cheie naturală de sincronizare, deci o redenumire necesită verificare.", False),
    "LOC003": ("warning", "locuri", "Loc fără uid de sincronizare", "Porniți MAIN-ul normal (inițializează uid-urile) înainte de prima sincronizare.", True),
    "LOC004": ("warning", "locuri", "Sectorul locului nu există în tabela de sectoare", "Creați sectorul sau corectați eticheta sectorului la loc.", False),
    "LOC005": ("error", "locuri", "Poligon invalid (JSON/structură/coordonate)", "Redesenați poligonul în editorul hărții.", False),
    "LOC006": ("warning", "locuri", "Poligon degenerat (sub 3 puncte, arie zero sau autointersectat)", "Redesenați poligonul în editorul hărții.", False),
    "LOC007": ("warning", "locuri", "Același poligon folosit de mai multe locuri", "Verificați pe hartă dacă locurile sunt duplicate sau unul are poligon copiat din greșeală.", False),
    "LOC008": ("warning", "locuri", "Poligon peste limita de sincronizare (puncte)", "Simplificați poligonul: serverul respinge peste limita de puncte.", False),
    "LOC009": ("warning", "locuri", "Posibil loc duplicat (același cod normalizat sau aceeași poziție)", "Comparați manual înregistrările; nu se contopesc automat.", False),
    "LOC010": ("info", "locuri", "Loc marcat „liber” deși are înhumați (capacitate epuizată sau necunoscută)", "Informativ: în MAIN starea locului este introdusă manual; verificați dacă locul trebuie marcat ocupat. Nu este eroare dacă locul mai are capacitate liberă.", False),
    "LOC011": ("warning", "locuri", "Valori numerice/JSON invalide la loc (suprafață, extra_data)", "Corectați valorile din fișa locului.", False),
    "LOC012": ("info", "locuri", "Marcaj „are înhumări” fără înhumați înregistrați", "Verificați dacă înhumații lipsesc din evidență sau marcajul este vechi.", False),
    "LOC013": ("warning", "locuri", "Câmp text peste limita de sincronizare", "Scurtați textul; serverul îl respinge peste limită.", False),
    # ---- sectoare
    "SEC001": ("error", "sectoare", "Poligon de sector invalid", "Redesenați sectorul în editorul hărții.", False),
    # ---- concesionari
    "CON001": ("error", "concesionari", "Concesionar fără nume și prenume", "Completați datele sau eliminați înregistrarea dacă este eroare de import.", False),
    "CON002": ("info", "concesionari", "Date de identificare/contact incomplete", "Completați când sunt disponibile; nu este obligatoriu.", False),
    "CON003": ("warning", "concesionari", "CNP invalid ca structură/cifră de control", "Verificați CNP-ul cu actul de identitate (valoarea nu apare în raport).", False),
    "CON004": ("warning", "concesionari", "Posibil același concesionar înregistrat de mai multe ori (CNP identic)", "Decideți manual contopirea; nu se face automat. Persoana poate rămâne asociată mai multor locuri printr-o singură înregistrare.", False),
    "CON005": ("info", "concesionari", "Același nume la mai mulți concesionari (nu este dovadă de duplicat)", "Informativ: omonimele sunt posibile; nu se contopesc fără alt identificator.", False),
    "CON006": ("warning", "concesionari", "Același nume și aceleași date de contact la concesionari diferiți fără CNP care să-i diferențieze", "Verificați dacă este aceeași persoană.", False),
    "CON007": ("error", "concesionari", "Conflict de identitate: același CNP, nume diferite", "Verificați care înregistrare este corectă (valorile CNP nu apar în raport).", False),
    "CON008": ("info", "concesionari", "Concesionar fără niciun contract", "Informativ: relația este opțională.", False),
    "CON009": ("warning", "concesionari", "Adresă de e-mail cu format invalid", "Corectați e-mailul.", False),
    # ---- contracte
    "CTR001": ("error", "contracte", "Contract fără concesionar și fără titular în text", "Asociați un concesionar sau completați titularul.", False),
    "CTR002": ("critical", "contracte", "Contract cu concesionar_id inexistent", "Reasociați contractul unui concesionar existent.", False),
    "CTR003": ("warning", "contracte", "Contract fără concesionar asociat (doar titular în text)", "Asociați contractul unui concesionar din evidență.", False),
    "CTR004": ("warning", "contracte", "Contract fără loc asociat", "Asociați contractul unui loc.", False),
    "CTR005": ("critical", "contracte", "Contract cu id_loc inexistent", "Reasociați contractul unui loc existent.", False),
    "CTR006": ("error", "contracte", "cod_loc al contractului nu corespunde locului asociat (id_loc)", "Alegeți locul corect; dacă id_loc este nul și cod_loc corespunde unui loc, legătura poate fi refăcută automat.", True),
    "CTR007": ("warning", "contracte", "Număr de contract lipsă", "Completați numărul contractului.", False),
    "CTR008": ("warning", "contracte", "Număr de contract folosit de mai multe contracte", "Verificați dacă sunt contracte diferite (ani/registre diferite) sau o dublare.", False),
    "CTR009": ("error", "contracte", "Contracte duplicate (aceleași număr, dată, loc și concesionar)", "Eliminați manual dublura după verificare.", False),
    "CTR010": ("error", "contracte", "Perioadă incoerentă (sfârșit înaintea începutului)", "Corectați datele contractului.", False),
    "CTR011": ("warning", "contracte", "Perioade suprapuse pentru același loc, cu concesionari diferiți", "Verificați care contract este în vigoare; poate fi o preluare legitimă dacă primul a încetat.", False),
    "CTR012": ("warning", "contracte", "Titularul din contract diferă de concesionarul asociat (nume)", "Aliniați titularul din contract cu concesionarul sau reasociați contractul.", False),
    "CTR013": ("error", "contracte", "CNP-ul titularului din contract diferă de al concesionarului asociat", "Verificați identitatea titularului (valorile nu apar în raport).", False),
    "CTR014": ("info", "contracte", "Parcela/rândul/numărul din contract diferă de datele curente ale locului", "Informativ: contractul păstrează datele de la semnare.", False),
    "CTR015": ("warning", "contracte", "Câmp text peste limita de sincronizare", "Scurtați textul.", False),
    # ---- înhumați
    "INH001": ("error", "inhumati", "Înhumat fără loc asociat", "Asociați înhumatul unui loc (crearea în aplicație cere loc).", False),
    "INH002": ("critical", "inhumati", "Înhumat cu id_loc inexistent", "Reasociați înhumatul unui loc existent.", False),
    "INH003": ("critical", "inhumati", "Înhumat cu contract_id inexistent", "Eliminați sau corectați legătura cu contractul.", False),
    "INH004": ("error", "inhumati", "Contractul înhumatului aparține altui loc", "Verificați locul înhumării și contractul asociat.", False),
    "INH005": ("error", "inhumati", "Înhumat fără nume și prenume", "Completați datele.", False),
    "INH006": ("error", "inhumati", "Date calendaristice incoerente (naștere/deces/înhumare/deshumare)", "Corectați datele după actele doveditoare.", False),
    "INH007": ("warning", "inhumati", "Dată invalidă sau în viitor", "Corectați data.", False),
    "INH008": ("error", "inhumati", "Același CNP la mai mulți înhumați", "O persoană nu poate fi înhumată de două ori; verificați dublura (valorile nu apar în raport).", False),
    "INH009": ("warning", "inhumati", "Posibil înhumat duplicat (același nume și dată a decesului)", "Comparați înregistrările; nu se contopesc automat.", False),
    "INH010": ("warning", "inhumati", "Număr de înhumați peste capacitatea locului", "Verificați capacitatea sau înhumații.", False),
    "INH011": ("warning", "inhumati", "CNP invalid ca structură/cifră de control", "Verificați CNP-ul cu actul (valoarea nu apare în raport).", False),
    "INH012": ("warning", "inhumati", "Data nașterii sau sexul nu corespund CNP-ului", "Verificați datele cu actul (valorile nu apar în raport).", False),
    "INH013": ("warning", "inhumati", "Câmp text peste limita de sincronizare", "Scurtați textul.", False),
    # ---- avize
    "AVZ001": ("critical", "avize", "Aviz cu cod_loc inexistent sau lipsă", "Reasociați avizul unui loc existent.", False),
    "AVZ002": ("error", "avize", "Aviz fără număr", "Completați numărul avizului.", False),
    "AVZ003": ("warning", "avize", "Același număr de aviz repetat pentru același loc", "Verificați dacă este o dublură.", False),
    "AVZ004": ("error", "avize", "Data finalizării înaintea datei începerii", "Corectați datele lucrării.", False),
    "AVZ005": ("warning", "avize", "Stare necunoscută (în afara planificata/in_lucru/finalizata)", "Alegeți una dintre stările standard; MAIN acceptă valori vechi libere.", False),
    "AVZ006": ("warning", "avize", "Stare incompatibilă cu datele disponibile", "Aliniați starea cu datele de început/finalizare.", False),
    "AVZ007": ("warning", "avize", "Dată de aviz/început/finalizare invalidă", "Corectați data.", False),
    "AVZ008": ("warning", "avize", "Câmp text peste limita de sincronizare", "Scurtați textul.", False),
    # ---- documente
    "DOC001": ("warning", "documente", "Tip de entitate necunoscut la document", "Verificați proveniența; tipurile cunoscute sunt loc, inhumat, concesionar, contract, aviz, cerere.", False),
    "DOC002": ("critical", "documente", "Document asociat unei entități inexistente", "Reasociați documentul sau eliminați-l după verificare.", False),
    "DOC003": ("error", "documente", "Document fără fișier și fără identificator media", "Reîncărcați fișierul sau eliminați înregistrarea.", False),
    "DOC004": ("error", "documente", "Fișier referențiat lipsește de pe disc (fără copie în storage)", "Restaurați fișierul din backup sau reîncărcați-l.", False),
    "DOC005": ("info", "documente", "Fișierul nu este pe disc, dar există copie în storage (media_id)", "Informativ: se aduce din storage la deschidere; nu poate fi verificat local.", False),
    "DOC006": ("info", "documente", "Metadate incomplete la document", "Completați tipul/descrierea când este posibil.", False),
    "DOC007": ("warning", "documente", "Document duplicat (aceeași entitate și același sha256)", "Eliminați manual duplicatul.", False),
    "DOC008": ("warning", "documente", "Tipul documentului nu se potrivește cu entitatea asociată", "Verificați asocierea documentului.", False),
    "DOC009": ("warning", "documente", "Dimensiunea fișierului diferă de cea înregistrată", "Verificați integritatea fișierului (poate fi modificat sau trunchiat).", False),
    "DOC010": ("critical", "documente", "Fotografie asociată unui loc inexistent", "Reasociați fotografia unui loc existent.", False),
    "DOC011": ("warning", "documente", "Mai multe fotografii marcate ca principală (cover) la același loc", "Păstrați o singură fotografie principală.", True),
    "DOC012": ("warning", "documente", "Document de loc referit prin id numeric în loc de cod_loc", "Corectați entity_id la cod_loc.", True),
    "DOC013": ("error", "documente", "Hash-ul fișierului diferă de sha256 înregistrat", "Fișier alterat sau înlocuit; restaurați din backup.", False),
    # ---- plăți
    "PLT001": ("error", "plati", "Plată fără loc și fără contract", "Asociați plata unui loc sau contract.", False),
    "PLT002": ("critical", "plati", "Plată cu cod_loc inexistent", "Reasociați plata unui loc existent.", False),
    "PLT003": ("critical", "plati", "Plată cu contract_id inexistent", "Reasociați plata unui contract existent.", False),
    "PLT004": ("error", "plati", "Locul plății diferă de locul contractului asociat", "Verificați asocierea plății.", False),
    "PLT005": ("warning", "plati", "Sumă lipsă sau zero", "Verificați chitanța.", False),
    "PLT006": ("error", "plati", "Sumă nenumerică (NaN/infinit) sau de tip invalid", "Corectați suma.", False),
    "PLT007": ("info", "plati", "Sumă negativă (posibilă corecție/stornare; modelul nu o definește explicit)", "Informativ: schema nu are câmp dedicat pentru stornări.", False),
    "PLT008": ("warning", "plati", "Dată a plății lipsă, invalidă sau în viitor", "Corectați data.", False),
    "PLT009": ("warning", "plati", "Posibile plăți duplicate (aceleași loc, sumă, dată, tip, chitanță)", "Verificați chitanțele.", False),
    "PLT010": ("warning", "plati", "Același număr de chitanță la plăți diferite", "Verificați chitanțele; un număr ar trebui să identifice o singură încasare.", False),
    # ---- sincronizare / formate
    "SYN001": ("info", "sincronizare", "Înregistrare fără uid de sincronizare", "Se inițializează la pornirea MAIN; fără uid nu se sincronizează.", True),
    "SYN002": ("warning", "sincronizare", "uid duplicat în același tabel", "Corectați manual; uid-ul identifică înregistrarea în Cloud/Mobile.", False),
    "DAT002": ("warning", "sincronizare", "Dată invalidă (format nerecunoscut sau dată inexistentă)", "Corectați data în formatul zz.ll.aaaa.", False),
    "DAT001": ("info", "sincronizare", "Dată în format ISO (aaaa-ll-zz) într-un câmp în care MAIN folosește zz.ll.aaaa", "Format amestecat: MAIN scrie zz.ll.aaaa, Mobile lucrează cu aaaa-ll-zz. Normalizarea se decide separat (necesită aprobare).", True),
    # ---- financiar
    "FIN000": ("info", "financiar", "Soldul nu poate fi verificat: schema nu conține obligații de plată", "Există doar plăți efectuate (tabela plati). Definiția obligațiilor/soldului trebuie stabilită de utilizator înainte de orice verificare de sold.", False),
    # ---- meta
    "META001": ("info", "sincronizare", "Verificare omisă (tabel/coloană lipsă în schema analizată)", "Informativ: schema este mai veche; verificarea nu se aplică.", False),
    "META002": ("info", "sincronizare", "Număr maxim de probleme atins pentru o verificare (lista este trunchiată)", "Rulați cu o limită mai mare pentru lista completă; sumarul rămâne corect.", False),
}


@dataclass
class Issue:
    code: str
    severity: str
    category: str
    entity: str
    record_id: Optional[str]
    description: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""
    auto_fixable: bool = False
    title: str = ""
    uid: Optional[str] = None
    cod_loc: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"code": self.code, "severity": self.severity, "severity_ro": SEVERITY_RO[self.severity], "category": self.category,
             "category_ro": CATEGORY_RO.get(self.category, self.category), "entity": self.entity, "record_id": self.record_id,
             "uid": self.uid, "cod_loc": self.cod_loc, "title": self.title, "description": self.description,
             "evidence": self.evidence, "recommendation": self.recommendation, "auto_fixable_future": self.auto_fixable}
        return redact_obj(d)


@dataclass
class Report:
    issues: List[Issue]
    meta: Dict[str, Any]

    def summary(self) -> Dict[str, Any]:
        """Sumarul numără TOATE problemele găsite (meta.found), chiar dacă lista detaliată a fost trunchiată de limita per verificare."""
        found = self.meta.get("found") or {}
        by_sev = {s: 0 for s in SEVERITIES}
        by_cat: Dict[str, Dict[str, int]] = {}
        by_code: Dict[str, int] = {}
        fixable = 0
        if found:
            for key, n in found.items():
                code, sev = key.split("|")
                cat = CATALOG[code][1]
                by_sev[sev] += n
                by_cat.setdefault(cat, {s: 0 for s in SEVERITIES})[sev] += n
                by_code[code] = by_code.get(code, 0) + n
                fixable += n if CATALOG[code][4] else 0
        else:
            for i in self.issues:
                by_sev[i.severity] += 1
                by_cat.setdefault(i.category, {s: 0 for s in SEVERITIES})[i.severity] += 1
                by_code[i.code] = by_code.get(i.code, 0) + 1
                fixable += 1 if i.auto_fixable else 0
        return {"total": sum(by_sev.values()), "listed": len(self.issues), "by_severity": by_sev, "by_category": by_cat,
                "by_code": dict(sorted(by_code.items())), "auto_fixable_future": fixable}

    def to_dict(self) -> Dict[str, Any]:
        return {"meta": redact_obj(self.meta), "summary": self.summary(), "issues": [i.to_dict() for i in self.issues]}
