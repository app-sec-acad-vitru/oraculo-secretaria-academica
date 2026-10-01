#!/usr/bin/env python3
"""
Oráculo da Secretaria Acadêmica — V10
Monitoramento específico de atos regulatórios de cursos de graduação.

Princípios:
- DOU/Imprensa Nacional é a fonte oficial de confirmação.
- e-MEC é fonte secundária de conferência, não substitui a publicação oficial.
- Hash/alteração de página NÃO é tratado como ato normativo.
- Nenhum registro entra como confirmado apenas porque contém "autorização",
  "reconhecimento" ou "renovação".
- O monitor é abrangente: não limita a busca à UNIASSELVI, Vitru ou qualquer IES.

Fontes de entrada suportadas:
1) DOU público via URL configurável (tentativa de consulta HTML).
2) Arquivos XML do INLABS já baixados pelo usuário/ambiente.
3) monitoring/dou_candidates.json para testes controlados.

Variáveis opcionais:
DOU_SEARCH_URL_TEMPLATE
INLABS_XML_DIR
DOU_LOOKBACK_DAYS (padrão 2)
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "monitoring"
OUT_FILE = MONITORING / "atos_regulatorios.json"
LOG_FILE = MONITORING / "atos_regulatorios_log.json"
CANDIDATES_FILE = MONITORING / "dou_candidates.json"

DEFAULT_DOU_TEMPLATE = "https://www.in.gov.br/consulta/-/buscar/dou?q={query}"

# Termos deliberadamente restritivos para reduzir falso positivo.
ACT_PATTERNS = [
    ("Renovação de Reconhecimento", re.compile(r"\brenova(?:ç|c)[ãa]o\s+de\s+reconhecimento\b", re.I)),
    ("Reconhecimento", re.compile(r"\breconhecimento\b", re.I)),
    ("Autorização", re.compile(r"\bautoriza(?:ç|c)[ãa]o\b", re.I)),
]
ADITAMENTO_TERMS = re.compile(r"\baditamento\b|\baditamento\s+de\b", re.I)
GRADUATION_TERMS = re.compile(
    r"\bcurso(?:s)?\s+(?:de\s+)?gradua(?:ç|c)[ãa]o\b|\bgradua(?:ç|c)[ãa]o\b",
    re.I,
)
MODALITY_TERMS = {
    "EaD": re.compile(r"\bEaD\b|\beduca(?:ç|c)[ãa]o\s+a\s+dist[âa]ncia\b", re.I),
    "Semipresencial": re.compile(r"\bsemipresencial\b", re.I),
    "Presencial": re.compile(r"\bpresencial\b", re.I),
}
DEGREE_TERMS = {
    "Licenciatura": re.compile(r"\blicenciatura\b", re.I),
    "Bacharelado": re.compile(r"\bbacharelado\b", re.I),
    "Tecnológico": re.compile(r"\btecn[oó]logo\b|\btecnologia\b", re.I),
}
ACT_NUMBER_RE = re.compile(
    r"\b(?:PORTARIA(?:\s+(?:MEC|SERES/MEC|SERES))?|PORTARIA\s+MEC|RESOLUÇÃO|DESPACHO)"
    r"\s*(?:N[º°o]\s*)?(\d{1,5})\s*(?:/|-)\s*(\d{4})\b",
    re.I,
)
DATE_RE = re.compile(
    r"\b(?:de\s+)?(\d{1,2})\s+de\s+([A-Za-zçãéêôú]+)\s+de\s+(\d{4})\b",
    re.I,
)
ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
URL_RE = re.compile(r'https?://[^\s"<>]+')

MONTHS = {
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
}

@dataclass
class Candidate:
    title: str
    url: str
    published_text: str = ""
    body: str = ""
    source: str = "unknown"

@dataclass
class ActRecord:
    id: str
    tipo_ato: str
    numero_ato: str
    ano: int | None
    data_publicacao: str | None
    ies: str | None
    mantenedora: str | None
    curso: str | None
    grau: str | None
    modalidade: str | None
    local: str | None
    situacao: str
    assunto: str
    fonte_oficial: str
    fonte_conferencia: str | None
    confirmado: bool
    classificacao: str
    evidencia: str
    coletado_em: str

def normalize(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )

def official_url(url: str) -> bool:
    u = (url or "").lower()
    return "in.gov.br/" in u or "pesquisa.in.gov.br/" in u

def fetch(url: str, timeout: int = 30) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Oraculo-Secretaria-Academica-V10/1.0 (+monitoramento regulatorio)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def extract_links(page: str) -> list[str]:
    links = []
    for m in URL_RE.findall(page or ""):
        u = html.unescape(m).rstrip(".,);]")
        if official_url(u) and u not in links:
            links.append(u)
    # hrefs without protocol
    for m in re.findall(r'href=["\']([^"\']+)["\']', page or "", flags=re.I):
        u = html.unescape(m)
        if u.startswith("/web/dou/-/"):
            u = "https://www.in.gov.br" + u
        if u.startswith("http") and official_url(u) and u not in links:
            links.append(u)
    return links

def candidate_queries() -> list[str]:
    return [
        '"autorização" "curso de graduação" MEC',
        '"reconhecimento" "curso de graduação" MEC',
        '"renovação de reconhecimento" "curso de graduação" MEC',
        '"aditamento" "curso de graduação" MEC',
    ]

def load_seed_candidates() -> list[Candidate]:
    if not CANDIDATES_FILE.exists():
        return []
    try:
        raw = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[WARN] Falha lendo {CANDIDATES_FILE}: {exc}")
        return []
    out = []
    for item in raw if isinstance(raw, list) else raw.get("candidates", []):
        if not isinstance(item, dict):
            continue
        if item.get("url"):
            out.append(Candidate(
                title=str(item.get("title", "")),
                url=str(item["url"]),
                published_text=str(item.get("published_text", "")),
                body=str(item.get("body", "")),
                source="seed",
            ))
    return out

def load_inlabs() -> list[Candidate]:
    folder = os.getenv("INLABS_XML_DIR")
    if not folder:
        return []
    p = Path(folder)
    if not p.exists():
        print(f"[WARN] INLABS_XML_DIR não existe: {p}")
        return []
    out = []
    for xml_file in sorted(p.rglob("*.xml")):
        try:
            root = ET.parse(xml_file).getroot()
            text = " ".join(t.strip() for t in root.itertext() if t and t.strip())
            urls = [u for u in URL_RE.findall(text) if official_url(u)]
            url = urls[0] if urls else ""
            title = text[:300]
            out.append(Candidate(title=title, url=url, body=text, source="inlabs"))
        except Exception as exc:
            print(f"[WARN] XML inválido {xml_file}: {exc}")
    return out

def collect_from_dou() -> list[Candidate]:
    template = os.getenv("DOU_SEARCH_URL_TEMPLATE", DEFAULT_DOU_TEMPLATE)
    out, seen = [], set()
    for query in candidate_queries():
        url = template.format(query=quote_plus(query))
        try:
            page = fetch(url)
            clean = normalize(page)
            links = extract_links(page)
            # Se a busca devolver uma página única/JS, ainda guardamos a própria página
            # como evidência candidata; confirmação dependerá do conteúdo oficial.
            for link in links:
                if link not in seen:
                    seen.add(link)
                    out.append(Candidate(title="", url=link, body="", source="dou_search"))
            if links:
                print(f"[DOU] {query}: {len(links)} links oficiais encontrados")
            else:
                print(f"[DOU] {query}: nenhum link oficial extraído")
            time.sleep(0.5)
        except Exception as exc:
            print(f"[WARN] Falha na consulta DOU '{query}': {exc}")
    return out

def parse_date(text: str) -> str | None:
    m = ISO_DATE_RE.search(text or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = DATE_RE.search(text or "")
    if m:
        day, month, year = int(m.group(1)), strip_accents(m.group(2)).lower(), int(m.group(3))
        if month in MONTHS:
            return f"{year:04d}-{MONTHS[month]:02d}-{day:02d}"
    return None

def find_act_number(text: str) -> tuple[str, int | None]:
    m = ACT_NUMBER_RE.search(text or "")
    if not m:
        return "", None
    return m.group(1), int(m.group(2))

def find_tipo(text: str) -> str | None:
    # Ordem importa: renovação antes de reconhecimento.
    if ACT_PATTERNS[0][1].search(text):
        return ACT_PATTERNS[0][0]
    if ACT_PATTERNS[1][1].search(text):
        return ACT_PATTERNS[1][0]
    if ACT_PATTERNS[2][1].search(text):
        return ACT_PATTERNS[2][0]
    if ADITAMENTO_TERMS.search(text):
        return "Aditamento"
    return None

def find_modality(text: str) -> str | None:
    for label, pat in MODALITY_TERMS.items():
        if pat.search(text):
            return label
    return None

def find_degree(text: str) -> str | None:
    for label, pat in DEGREE_TERMS.items():
        if pat.search(text):
            return label
    return None

def extract_ies(text: str) -> str | None:
    patterns = [
        r"\bINTERESSAD[OA]\s*:\s*([^.;]+)",
        r"\bMANTIDA\s*:\s*([^.;]+)",
        r"\bINSTITUI(?:Ç|C)[ÃA]O(?:\s+DE\s+EDUCA(?:Ç|C)[ÃA]O\s+SUPERIOR)?\s*[:\-]\s*([^.;]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            value = m.group(1).strip()
            if 3 <= len(value) <= 180:
                return value
    # Heurística conservadora: nomes com UNIVERSIDADE/CENTRO/FACULDADE/INSTITUTO.
    m = re.search(
        r"\b((?:UNIVERSIDADE|CENTRO UNIVERSIT[ÁA]RIO|FACULDADE|INSTITUTO)[A-ZÁÉÍÓÚÃÕÇ0-9 .,&'’\-]{5,160})",
        text,
        re.I,
    )
    return re.sub(r"\s+", " ", m.group(1)).strip(" .,-") if m else None

def extract_course(text: str) -> str | None:
    pats = [
        r"\bcurso(?:\s+de)?\s+([A-ZÁÉÍÓÚÃÕÇ0-9][^.;:]{2,120}?)(?:\s*[-–]\s*(?:bacharelado|licenciatura|tecn[oó]logo)|,|\.)",
        r"\bcurso\s+de\s+gradua(?:ç|c)[ãa]o\s+em\s+([^.;:]{2,120})",
        r"\b(?:bacharelado|licenciatura|tecn[oó]logo)\s+em\s+([^.;:]{2,120})",
    ]
    for pat in pats:
        m = re.search(pat, text, re.I)
        if m:
            value = re.sub(r"\s+", " ", m.group(1)).strip(" .,-")
            if 3 <= len(value) <= 150:
                return value
    return None

def build_record(c: Candidate, detail: str) -> ActRecord | None:
    text = normalize(" ".join([c.title, c.published_text, c.body, detail]))
    tipo = find_tipo(text)
    if not tipo:
        return None

    has_grad = bool(GRADUATION_TERMS.search(text))
    has_degree = bool(find_degree(text))
    has_course_signal = bool(re.search(r"\bcurso\b", text, re.I))

    # Regra crítica: palavras soltas não bastam.
    if tipo in {"Autorização", "Reconhecimento", "Renovação de Reconhecimento"}:
        if not (has_grad and has_course_signal and has_degree):
            return None

    numero, ano = find_act_number(text)
    data = parse_date(text)
    modalidade = find_modality(text)
    grau = find_degree(text)
    ies = extract_ies(text)
    curso = extract_course(text)

    # Confirmação só ocorre quando temos URL oficial + número + curso + IES + sinal de graduação.
    confirmed = bool(
        official_url(c.url)
        and numero
        and ano
        and curso
        and ies
        and has_grad
    )

    if not confirmed:
        classificacao = "ato_normativo_candidato"
        situacao = "Validação pendente"
    else:
        classificacao = "alteracao_normativa_confirmada"
        situacao = {
            "Autorização": "Autorizado",
            "Reconhecimento": "Reconhecido",
            "Renovação de Reconhecimento": "Renovação de reconhecimento",
            "Aditamento": "Aditamento",
        }.get(tipo, "Ato confirmado")

    # Não inferir mantenedora/local sem evidência explícita.
    mantenedora = None
    local = None
    for label, target in [
        ("MANTENEDORA", "mantenedora"),
        ("LOCAL DE OFERTA", "local"),
        ("ENDEREÇO", "local"),
    ]:
        m = re.search(rf"\b{label}\s*[:\-]\s*([^.;]+)", text, re.I)
        if m:
            value = re.sub(r"\s+", " ", m.group(1)).strip()
            if target == "mantenedora":
                mantenedora = value[:180]
            else:
                local = value[:180]

    fingerprint = hashlib.sha256(
        "|".join([
            str(numero), str(ano), str(ies or ""), str(curso or ""), c.url
        ]).encode("utf-8")
    ).hexdigest()[:20]

    evidence = text[:1200]
    return ActRecord(
        id=f"DOU-{fingerprint}",
        tipo_ato=tipo,
        numero_ato=numero,
        ano=ano,
        data_publicacao=data,
        ies=ies,
        mantenedora=mantenedora,
        curso=curso,
        grau=grau,
        modalidade=modalidade,
        local=local,
        situacao=situacao,
        assunto=f"{tipo} de curso de graduação",
        fonte_oficial=c.url,
        fonte_conferencia="https://emec.mec.gov.br/",
        confirmado=confirmed,
        classificacao=classificacao,
        evidencia=evidence,
        coletado_em=datetime.now(timezone.utc).isoformat(),
    )

def load_existing() -> list[dict]:
    if not OUT_FILE.exists():
        return []
    try:
        raw = json.loads(OUT_FILE.read_text(encoding="utf-8"))
        return raw.get("atos", []) if isinstance(raw, dict) else []
    except Exception:
        return []

def main() -> int:
    MONITORING.mkdir(parents=True, exist_ok=True)
    existing = load_existing()
    existing_by_id = {x.get("id"): x for x in existing if isinstance(x, dict) and x.get("id")}

    candidates = []
    candidates.extend(load_seed_candidates())
    candidates.extend(load_inlabs())
    candidates.extend(collect_from_dou())

    # dedup por URL
    dedup = {}
    for c in candidates:
        if c.url and c.url not in dedup:
            dedup[c.url] = c
    candidates = list(dedup.values())

    new_records = []
    for c in candidates:
        detail = ""
        if c.url and official_url(c.url) and not c.body:
            try:
                detail = fetch(c.url)
            except Exception as exc:
                print(f"[WARN] Não foi possível abrir ato {c.url}: {exc}")
        record = build_record(c, detail)
        if record:
            existing_by_id[record.id] = asdict(record)
            if record.id not in {r.get("id") for r in existing}:
                new_records.append(asdict(record))

    all_records = list(existing_by_id.values())
    all_records.sort(key=lambda x: (x.get("data_publicacao") or "", x.get("numero_ato") or ""), reverse=True)

    payload = {
        "version": "V10",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total_atos": len(all_records),
        "confirmados": sum(1 for x in all_records if x.get("confirmado")),
        "pendentes": sum(1 for x in all_records if not x.get("confirmado")),
        "atos": all_records,
    }
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    log = {
        "version": "V10",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "candidates_received": len(candidates),
        "new_records": len(new_records),
        "confirmed": sum(1 for x in new_records if x.get("confirmado")),
        "pending_validation": sum(1 for x in new_records if not x.get("confirmado")),
        "source_policy": {
            "primary": "DOU/Imprensa Nacional",
            "secondary": "e-MEC",
            "scope": "Todos os cursos de graduação, sem restrição por IES, mantenedora, modalidade ou localidade.",
        },
        "new_records": new_records,
    }
    LOG_FILE.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] Candidatos: {len(candidates)} | Novos registros: {len(new_records)} | Confirmados: {log['confirmed']}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
