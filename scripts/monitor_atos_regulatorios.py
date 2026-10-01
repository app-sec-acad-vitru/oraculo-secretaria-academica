#!/usr/bin/env python3
"""
Oráculo da Secretaria Acadêmica — V10.2
Captura real de atos regulatórios de graduação publicados no DOU.

V10.2:
- Usa INLABS como camada estruturada de ingestão quando INLABS_EMAIL/PASSWORD
  estiverem configurados no ambiente (GitHub Actions Secrets).
- Mantém a publicação individual do DOU como fonte oficial de confirmação.
- Varre DO1 e DO1E de hoje e do dia anterior.
- Não depende do endpoint antigo /consulta/-/buscar/dou, que pode retornar
  páginas vazias/indisponíveis.
- Remove registros antigos cuja fonte não seja uma publicação individual.
- Não confirma atos apenas por palavras soltas: exige ato + número + data +
  IES + curso + grau + sinais de graduação + URL individual do DOU.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import zipfile
import html
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from http.cookiejar import CookieJar
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "monitoring"
OUT_FILE = MONITORING / "atos_regulatorios.json"
LOG_FILE = MONITORING / "atos_regulatorios_log.json"
CANDIDATES_FILE = MONITORING / "dou_candidates.json"

INLABS_LOGIN = "https://inlabs.in.gov.br/logar.php"
INLABS_ACCESS = "https://inlabs.in.gov.br/acessar.php"
INLABS_DOWNLOAD = "https://inlabs.in.gov.br/index.php?p={date}&dl={date}-{section}.zip"
INLABS_ORIGEM = "736372697074"

ACT_PATTERNS = [
    ("Renovação de Reconhecimento", re.compile(r"\brenova(?:ção|cao)\s+de\s+reconhecimento\b", re.I)),
    ("Reconhecimento", re.compile(r"\breconhecimento\b", re.I)),
    ("Autorização", re.compile(r"\bautoriz(?:ação|acao)\b", re.I)),
    ("Aditamento", re.compile(r"\baditamento\b", re.I)),
]

DEGREE_PATTERNS = [
    ("Licenciatura", re.compile(r"\blicenciatura\b", re.I)),
    ("Bacharelado", re.compile(r"\bbacharelado\b", re.I)),
    ("Tecnológico", re.compile(r"\btecn[oó]logo\b|\btecnologia\b", re.I)),
]

MODALITY_PATTERNS = [
    ("EaD", re.compile(r"\bEaD\b|educação a distância|educacao a distancia", re.I)),
    ("Semipresencial", re.compile(r"\bsemipresencial\b", re.I)),
    ("Presencial", re.compile(r"\bpresencial\b", re.I)),
]

MONTHS = {
    "janeiro":1,"fevereiro":2,"março":3,"marco":3,"abril":4,"maio":5,"junho":6,
    "julho":7,"agosto":8,"setembro":9,"outubro":10,"novembro":11,"dezembro":12
}

@dataclass
class Candidate:
    title: str
    url: str
    body: str
    published_text: str = ""
    source: str = "inlabs"

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
    fonte_conferencia: str
    confirmado: bool
    classificacao: str
    evidencia: str
    coletado_em: str

def norm(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<script\b[^>]*>.*?</script>", " ", s, flags=re.I|re.S)
    s = re.sub(r"<style\b[^>]*>.*?</style>", " ", s, flags=re.I|re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def is_dou_publication_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return (
        "pesquisa.in.gov.br/imprensa/servlet/inpdfviewer" in u
        or "in.gov.br/web/dou/-/" in u
    )

def http_session():
    jar = CookieJar()
    opener = __import__("urllib.request", fromlist=["build_opener"]).build_opener(
        __import__("urllib.request", fromlist=["HTTPCookieProcessor"]).HTTPCookieProcessor(jar)
    )
    return opener

def http_request(opener, url, data=None, headers=None, timeout=60):
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept-Language": "pt-BR,pt;q=0.9",
    }
    if headers:
        h.update(headers)
    req = Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    with opener.open(req, timeout=timeout) as r:
        return r.status, r.headers, r.read()

def inlabs_login(opener):
    email = os.getenv("INLABS_EMAIL", "").strip()
    password = os.getenv("INLABS_PASSWORD", "").strip()
    if not email or not password:
        return False, "Credenciais INLABS não configuradas."

    try:
        # Warm-up
        try:
            http_request(opener, INLABS_ACCESS, timeout=30)
        except Exception:
            pass

        payload = urlencode({"email": email, "password": password}).encode()
        status, headers, body = http_request(
            opener, INLABS_LOGIN, data=payload,
            headers={"Content-Type":"application/x-www-form-urlencoded", "Origem":INLABS_ORIGEM},
            timeout=30
        )
        text = body.decode("utf-8", "ignore")
        if re.search(r"tente mais tarde|#\s*01", text, re.I):
            return False, "INLABS informou bloqueio temporário."
        if status != 200:
            return False, f"Login INLABS HTTP {status}."
        # O cookie é mantido pelo CookieJar; presença do nome é o melhor sinal.
        # Se o portal mudou o cookie, o download abaixo ainda valida a sessão.
        return True, "Login INLABS realizado."
    except Exception as exc:
        return False, f"Falha no login INLABS: {exc}"

def parse_xml_article(xml_bytes, file_name, edition_date, section):
    root = ET.fromstring(xml_bytes)

    def local(tag):
        return tag.split("}")[-1].lower()

    article = root
    for el in root.iter():
        if local(el.tag) == "article":
            article = el
            break

    attrs = {k.lower(): v for k,v in article.attrib.items()}

    def first_text(names):
        wanted = {n.lower() for n in names}
        for el in article.iter():
            if local(el.tag) in wanted:
                return norm("".join(el.itertext()))
        return ""

    identifica = first_text(["Identifica"])
    titulo = first_text(["Titulo"])
    ementa = first_text(["Ementa"])
    subtitulo = first_text(["SubTitulo","Subtitulo"])
    texto_el = None
    for el in article.iter():
        if local(el.tag) == "texto":
            texto_el = el
            break
    texto = norm("".join(texto_el.itertext()))[:30000] if texto_el is not None else ""

    title = identifica or titulo or ementa or re.sub(r"\.xml$","",file_name,flags=re.I)
    url_title = attrs.get("name") or attrs.get("urltitle") or ""
    if url_title:
        link = "https://www.in.gov.br/web/dou/-/" + url_title
    else:
        n = section.replace("DO","").replace("E","")
        link = f"https://www.in.gov.br/web/dou/-/dou/{edition_date}/secao-{n}/"

    return {
        "title": norm(title),
        "subtitle": norm(subtitulo),
        "ementa": norm(ementa),
        "orgao": norm(attrs.get("artcategory","") or first_text(["NomeOrgao","Orgao"])),
        "texto": texto,
        "link": link,
        "published": attrs.get("pubdate","") or first_text(["pubDate","Data"]) or edition_date,
        "arttype": attrs.get("arttype",""),
        "pubname": attrs.get("pubname",""),
        "file": file_name,
        "section": section,
        "date": edition_date,
    }

def download_section(opener, date_iso, section):
    url = INLABS_DOWNLOAD.format(date=date_iso, section=section)
    try:
        status, headers, body = http_request(opener, url, timeout=120)
        if status != 200 or not body.startswith(b"PK"):
            return None, f"HTTP {status} ou conteúdo não ZIP"
        return body, None
    except Exception as exc:
        return None, str(exc)

def fetch_inlabs_days(days=2):
    opener = http_session()
    ok, msg = inlabs_login(opener)
    if not ok:
        print("[INLABS]", msg)
        return [], {"status":"indisponivel", "message":msg}

    candidates = []
    errors = []
    today = datetime.now(timezone.utc).date() - timedelta(hours=3)
    # GitHub Actions UTC -> Brasília; usando UTC-3 sem depender de pacote externo.
    today = datetime.now(timezone(timedelta(hours=-3))).date()

    for offset in range(days):
        d = today - timedelta(days=offset)
        date_iso = d.isoformat()
        for section in ("DO1","DO1E"):
            blob, err = download_section(opener, date_iso, section)
            if err:
                errors.append(f"{date_iso}/{section}: {err}")
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(blob)) as z:
                    for name in z.namelist():
                        if not name.lower().endswith(".xml"):
                            continue
                        try:
                            item = parse_xml_article(z.read(name), name, date_iso, section)
                            text = norm(" ".join([
                                item["title"], item["ementa"], item["orgao"], item["texto"], item["arttype"]
                            ]))
                            # Redução inicial antes do parser detalhado.
                            if re.search(r"SERES|SECRETARIA DE REGULAÇÃO E SUPERVISÃO DA EDUCAÇÃO SUPERIOR", text, re.I) and \
                               re.search(r"reconhecimento|autorização|autorizacao|aditamento", text, re.I) and \
                               re.search(r"curso|graduação|graduacao|bacharelado|licenciatura|tecnólogo|tecnologia", text, re.I):
                                candidates.append(Candidate(
                                    title=item["title"], url=item["link"], body=text,
                                    published_text=item["published"], source="inlabs"
                                ))
                        except Exception as exc:
                            errors.append(f"{date_iso}/{section}/{name}: XML {exc}")
            except zipfile.BadZipFile:
                errors.append(f"{date_iso}/{section}: ZIP inválido")

    return candidates, {"status":"ok", "message":msg, "errors":errors}

def parse_act(text):
    tipo = None
    for label, pat in ACT_PATTERNS:
        if pat.search(text):
            tipo = label
            break
    if not tipo:
        return None

    # Formatos: Nº 613, de 13 de novembro de 2024 / N° 613 DE 13...
    m = re.search(r"\b(?:PORTARIA|RESOLUÇÃO|DESPACHO)[^.\n]{0,100}?\bN[º°o]?\s*(\d{1,6})\s*(?:,|\-|–)?\s*DE\s+(\d{1,2})\s+DE\s+([A-Za-zçãéêôú]+)\s+DE\s+(\d{4})", text, re.I)
    if m:
        number = m.group(1)
        day = int(m.group(2))
        month = MONTHS.get(m.group(3).lower())
        year = int(m.group(4))
        date = f"{year:04d}-{month:02d}-{day:02d}" if month else None
    else:
        m = re.search(r"\b(?:PORTARIA|RESOLUÇÃO|DESPACHO)[^.\n]{0,100}?\bN[º°o]?\s*(\d{1,6})\b", text, re.I)
        number = m.group(1) if m else ""
        m2 = re.search(r"\bDE\s+(\d{1,2})\s+DE\s+([A-Za-zçãéêôú]+)\s+DE\s+(\d{4})", text, re.I)
        if m2 and m2.group(2).lower() in MONTHS:
            date = f"{int(m2.group(3)):04d}-{MONTHS[m2.group(2).lower()]:02d}-{int(m2.group(1)):02d}"
            year = int(m2.group(3))
        else:
            date = None
            year = None
    return tipo, number, year, date

def extract_degree(text):
    for label, pat in DEGREE_PATTERNS:
        if pat.search(text):
            return label
    return None

def extract_modality(text):
    for label, pat in MODALITY_PATTERNS:
        if pat.search(text):
            return label
    return None

def extract_ies(text):
    patterns = [
        r"\bMANTIDA\s*[:\-]\s*([^.;]{3,180})",
        r"\bINTERESSAD[OA]\s*[:\-]\s*([^.;]{3,180})",
        r"\bINSTITUIÇÃO DE EDUCAÇÃO SUPERIOR\s*[:\-]\s*([^.;]{3,180})",
        r"\b((?:UNIVERSIDADE|CENTRO UNIVERSITÁRIO|FACULDADE|INSTITUTO FEDERAL)[A-ZÁÉÍÓÚÃÕÇ0-9 .,&'’\-]{5,180})",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            v = re.sub(r"\s+"," ",m.group(1)).strip(" .,-")
            if 3 <= len(v) <= 180:
                return v
    return None

def extract_course(text):
    patterns = [
        r"\bCURSO(?: SUPERIOR)?(?: DE GRADUAÇÃO)?\s*[:\-]\s*([A-ZÁÉÍÓÚÃÕÇ0-9][^;\n.]{2,140})",
        r"\bCURSO\s+DE\s+GRADUAÇÃO\s+EM\s+([^;\n.]{2,140})",
        r"\b(?:BACHARELADO|LICENCIATURA|TECNÓLOGO)\s+EM\s+([^;\n.]{2,140})",
        r"\bcurso\s+(?:superior\s+)?(?:de\s+)?(?:graduação\s+)?em\s+([^;\n.]{2,140})",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            v = re.sub(r"\s+"," ",m.group(1)).strip(" .,-")
            # Não aceitar trechos gigantes que claramente são o restante da ementa.
            if 3 <= len(v) <= 140:
                return v
    return None

def extract_mantenedora(text):
    m = re.search(r"\bMANTENEDORA\s*[:\-]\s*([^.;]{3,180})", text, re.I)
    return re.sub(r"\s+"," ",m.group(1)).strip(" .,-") if m else None

def extract_local(text):
    for label in ("LOCAL DE OFERTA","ENDEREÇO"):
        m = re.search(rf"\b{label}\s*[:\-]\s*([^.;]{{3,180}})", text, re.I)
        if m:
            return re.sub(r"\s+"," ",m.group(1)).strip(" .,-")
    return None

def build_record(c):
    text = norm(c.body)
    parsed = parse_act(text)
    if not parsed:
        return None
    tipo, numero, ano, data = parsed
    grau = extract_degree(text)
    modalidade = extract_modality(text)
    ies = extract_ies(text)
    curso = extract_course(text)

    # Requisitos mínimos de confirmação.
    has_grad_signal = bool(re.search(r"\bgradua(?:ção|cao)\b|\bcurso[s]?\s+superior(?:es)?\b", text, re.I))
    has_course_signal = bool(re.search(r"\bcurso\b", text, re.I))
    confirmed = bool(
        is_dou_publication_url(c.url)
        and numero
        and ano
        and data
        and ies
        and curso
        and grau
        and has_grad_signal
        and has_course_signal
    )

    fingerprint = hashlib.sha256("|".join([
        numero or "", str(ano or ""), ies or "", curso or "", c.url
    ]).encode()).hexdigest()[:20]

    return ActRecord(
        id=f"DOU-{fingerprint}",
        tipo_ato=tipo,
        numero_ato=numero,
        ano=ano,
        data_publicacao=data,
        ies=ies,
        mantenedora=extract_mantenedora(text),
        curso=curso,
        grau=grau,
        modalidade=modalidade,
        local=extract_local(text),
        situacao={
            "Autorização":"Autorizado",
            "Reconhecimento":"Reconhecido",
            "Renovação de Reconhecimento":"Renovação de reconhecimento",
            "Aditamento":"Aditamento",
        }.get(tipo, "Ato confirmado") if confirmed else "Validação pendente",
        assunto=f"{tipo} de curso de graduação",
        fonte_oficial=c.url,
        fonte_conferencia="https://emec.mec.gov.br/",
        confirmado=confirmed,
        classificacao="alteracao_normativa_confirmada" if confirmed else "ato_normativo_candidato",
        evidencia=text[:1600],
        coletado_em=datetime.now(timezone.utc).isoformat(),
    )

def valid_existing(r):
    if not isinstance(r, dict):
        return False
    url = str(r.get("fonte_oficial",""))
    return bool(url and is_dou_publication_url(url))

def load_existing():
    if not OUT_FILE.exists():
        return []
    try:
        raw = json.loads(OUT_FILE.read_text(encoding="utf-8"))
        return [r for r in raw.get("atos",[]) if valid_existing(r)]
    except Exception:
        return []

def load_manual_candidates():
    if not CANDIDATES_FILE.exists():
        return []
    try:
        raw = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
        items = raw if isinstance(raw,list) else raw.get("candidates",[])
        out=[]
        for x in items:
            if isinstance(x,dict) and is_dou_publication_url(str(x.get("url",""))):
                out.append(Candidate(str(x.get("title","")),str(x["url"]),norm(" ".join([
                    x.get("title",""),x.get("published_text",""),x.get("body","")
                ])),source="seed"))
        return out
    except Exception:
        return []

def main():
    MONITORING.mkdir(parents=True,exist_ok=True)
    existing = {r["id"]:r for r in load_existing() if r.get("id")}

    candidates, source_status = fetch_inlabs_days(days=2)
    candidates.extend(load_manual_candidates())

    # Dedup by publication URL.
    by_url={}
    for c in candidates:
        by_url.setdefault(c.url,c)

    new=[]
    for c in by_url.values():
        r=build_record(c)
        if not r:
            continue
        old=existing.get(r.id)
        existing[r.id]=asdict(r)
        if old is None:
            new.append(asdict(r))

    records=sorted(existing.values(),key=lambda x:(x.get("data_publicacao") or "",x.get("numero_ato") or ""),reverse=True)
    payload={
        "version":"V10.2",
        "updated_at":datetime.now(timezone.utc).isoformat(),
        "total_atos":len(records),
        "confirmados":sum(bool(x.get("confirmado")) for x in records),
        "pendentes":sum(not bool(x.get("confirmado")) for x in records),
        "atos":records,
    }
    OUT_FILE.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")

    log={
        "version":"V10.2",
        "executed_at":datetime.now(timezone.utc).isoformat(),
        "source_status":source_status,
        "candidates_received":len(by_url),
        "new_records":new,
        "confirmed":sum(bool(x.get("confirmado")) for x in new),
        "pending_validation":sum(not bool(x.get("confirmado")) for x in new),
        "source_policy":{
            "primary":"DOU/Imprensa Nacional",
            "ingestion":"INLABS XML",
            "secondary":"e-MEC",
            "scope":"Todos os cursos de graduação, sem restrição por IES, mantenedora, modalidade ou localidade.",
            "confirmation_rule":"Publicação individual do DOU + evidência suficiente de ato, número, data, IES, curso e grau.",
        },
    }
    LOG_FILE.write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"[OK] candidatos={len(by_url)} totais={len(records)} novos={len(new)} confirmados_novos={log['confirmed']}")
    if source_status.get("errors"):
        print("[WARN] erros de coleta:", len(source_status["errors"]))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
