#!/usr/bin/env python3
"""
Oráculo da Secretaria Acadêmica — V10.6
Parser + triagem controlada de atos regulatórios no INLABS.

Esta versão:
- autentica no INLABS;
- baixa 30/09/2026 e 29/09/2026 (DO1/DO1E);
- lê todos os XMLs;
- extrai metadados do <article>;
- aplica triagem semântica por camadas;
- gera candidatos, sem confirmar atos;
- não altera atos_regulatorios.json.

A saída principal é monitoring/dou_candidates_v10_6.json.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin
from urllib.request import Request, build_opener
from http.cookiejar import CookieJar
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "monitoring"
OUT = MONITORING / "dou_candidates_v10_6.json"

BASE = "https://inlabs.in.gov.br"
ACCESS = f"{BASE}/acessar.php"
DOWNLOAD = f"{BASE}/index.php?p={{date}}&dl={{date}}-{{section}}.zip"
ORIGEM = "736372697074"

DATES = ["2026-09-30", "2026-09-29"]
SECTIONS = ["DO1", "DO1E"]

# Termos que indicam ato regulatório de graduação.
ACT_PATTERNS = [
    ("renovacao_reconhecimento", r"\brenova(?:ção|cao)\b.{0,100}\breconhecimento\b|\brenovação de reconhecimento\b|\brenovacao de reconhecimento\b"),
    ("reconhecimento", r"\breconhec(?:er|imento)\b.{0,120}\bcurso\b|\breconhecimento\b.{0,120}\bcurso\b"),
    ("autorizacao", r"\bautoriza(?:r|ção|cao)\b.{0,120}\b(?:curso|oferta|funcionamento)\b|\bautorização\b.{0,120}\bcurso\b|\bautorizacao\b.{0,120}\bcurso\b"),
    ("aditamento", r"\baditamento\b.{0,160}\b(?:curso|graduação|graduacao|ies|faculdade|universidade|centro universitário)\b"),
]

COURSE_PATTERNS = [
    r"\bcurso de graduação\b",
    r"\bcurso superior\b",
    r"\bcurso\b.{0,100}\b(?:bacharelado|licenciatura|tecnologia|tecnólogo|tecnologo)\b",
    r"\b(?:bacharelado|licenciatura|tecnologia|tecnólogo|tecnologo)\b",
]

HIGHER_ED_CONTEXT = [
    r"\bIES\b",
    r"\bInstituição de Ensino Superior\b",
    r"\binstituição de ensino superior\b",
    r"\bmantida\b",
    r"\bmantenedora\b",
    r"\bsecretaria de regulação e supervisão da educação superior\b",
    r"\bSERES\b",
    r"\bMinistério da Educação\b",
    r"\bMEC\b",
    r"\bSistema Federal de Ensino\b",
]

EXCLUDE_PATTERNS = [
    r"concurso público",
    r"conselho regional",
    r"conselho federal",
    r"processo seletivo",
    r"tomada de contas",
    r"pauta de julgamento",
]

class FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None
    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "form":
            self.current = {"action": a.get("action",""), "method": a.get("method","get").lower(), "inputs":[]}
            self.forms.append(self.current)
        elif self.current is not None and tag.lower() == "input":
            self.current["inputs"].append(a)
    def handle_endtag(self, tag):
        if tag.lower() == "form":
            self.current = None

def opener():
    jar = CookieJar()
    op = build_opener()
    from urllib.request import HTTPCookieProcessor
    op.add_handler(HTTPCookieProcessor(jar))
    return op

def request(op, url, data=None, headers=None, timeout=180):
    h = {
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
        "Accept":"*/*",
        "Accept-Language":"pt-BR,pt;q=0.9",
    }
    if headers: h.update(headers)
    req = Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    with op.open(req, timeout=timeout) as r:
        return r.status, dict(r.headers.items()), r.geturl(), r.read()

def visible(html):
    s = html.decode("utf-8","ignore") if isinstance(html,(bytes,bytearray)) else html
    s = re.sub(r"<script\b[^>]*>.*?</script>"," ",s,flags=re.I|re.S)
    s = re.sub(r"<style\b[^>]*>.*?</style>"," ",s,flags=re.I|re.S)
    s = re.sub(r"<[^>]+>"," ",s)
    return re.sub(r"\s+"," ",unescape(s)).strip()

def login(op):
    email = os.getenv("INLABS_EMAIL","").strip()
    password = os.getenv("INLABS_PASSWORD","").strip()
    if not email or not password:
        raise RuntimeError("Secrets INLABS_EMAIL/INLABS_PASSWORD não configurados.")

    _, _, _, body = request(op, ACCESS, timeout=30)
    parser = FormParser()
    parser.feed(body.decode("utf-8","ignore"))
    form = next((f for f in parser.forms if f.get("action","").lower().endswith("logar.php")), None)
    if not form:
        raise RuntimeError("Formulário logar.php não encontrado.")

    payload = {}
    for item in form["inputs"]:
        name = item.get("name","")
        typ = item.get("type","text").lower()
        if name and typ not in ("submit","button","file"):
            payload[name] = item.get("value","")
    payload["email"] = email
    payload["password"] = password

    action = urljoin(ACCESS, form.get("action") or "logar.php")
    _, _, final_url, body2 = request(
        op, action, data=urlencode(payload).encode(),
        headers={"Content-Type":"application/x-www-form-urlencoded","Referer":ACCESS,"Origin":BASE,"Origem":ORIGEM},
        timeout=30
    )
    txt = visible(body2).lower()
    if "acessar.php" in final_url.lower() or not re.search(r"minha conta|sair|logout", txt):
        raise RuntimeError("Falha na autenticação INLABS.")

def strip_html_text(root):
    chunks = []
    for el in root.iter():
        if el.text:
            chunks.append(el.text)
        if el.tail:
            chunks.append(el.tail)
    return re.sub(r"\s+"," ",unescape(" ".join(chunks))).strip()

def local(tag):
    return tag.split("}")[-1].lower()

def article_data(xml_bytes, filename):
    root = ET.fromstring(xml_bytes)
    article = next((e for e in root.iter() if local(e.tag) == "article"), None)
    if article is None:
        return None

    attrs = {str(k): str(v) for k,v in article.attrib.items()}
    text = strip_html_text(article)

    title = ""
    ementa = ""
    identifica = ""
    for e in article.iter():
        l = local(e.tag)
        t = re.sub(r"\s+"," ", "".join(e.itertext())).strip()
        if l == "titulo" and t: title = t
        elif l == "ementa" and t: ementa = t
        elif l == "identifica" and t: identifica = t

    # Alguns XMLs dividem a mesma matéria em páginas/anexos.
    # idOficio identifica o agrupamento editorial quando disponível.
    return {
        "filename": filename,
        "id": attrs.get("id"),
        "idMateria": attrs.get("idMateria"),
        "idOficio": attrs.get("idOficio"),
        "name": attrs.get("name"),
        "pubName": attrs.get("pubName"),
        "artType": attrs.get("artType"),
        "pubDate": attrs.get("pubDate"),
        "artCategory": attrs.get("artCategory"),
        "numberPage": attrs.get("numberPage"),
        "editionNumber": attrs.get("editionNumber"),
        "pdfPage": attrs.get("pdfPage"),
        "artNotes": attrs.get("artNotes"),
        "identifica": identifica[:1200],
        "titulo": title[:1500],
        "ementa": ementa[:2500],
        "text": text,
    }

def classify(item):
    text = item["text"].lower()
    title = (item["identifica"] + " " + item["titulo"] + " " + item["ementa"]).lower()
    reasons = []
    acts = []

    for label, pattern in ACT_PATTERNS:
        if re.search(pattern, title + " " + text, flags=re.I|re.S):
            acts.append(label)
            reasons.append(f"ato:{label}")

    course_hits = sum(bool(re.search(p, title + " " + text, flags=re.I|re.S)) for p in COURSE_PATTERNS)
    context_hits = sum(bool(re.search(p, title + " " + text, flags=re.I|re.S)) for p in HIGHER_ED_CONTEXT)
    exclude_hits = [p for p in EXCLUDE_PATTERNS if re.search(p, title + " " + text, flags=re.I|re.S)]

    # Pontuação deliberadamente conservadora.
    score = 0
    score += min(len(acts) * 3, 8)
    score += min(course_hits * 2, 6)
    score += min(context_hits * 2, 6)
    if re.search(r"\bSERES\b|secretaria de regulação e supervisão da educação superior", title + " " + text, re.I):
        score += 4
    if re.search(r"\bMEC\b|Ministério da Educação", title + " " + text, re.I):
        score += 2
    if exclude_hits:
        score -= min(4, len(exclude_hits))

    # Critério de candidato:
    # - precisa de ato regulatório + contexto de curso superior;
    # - ou ato regulatório + SERES/MEC + vocabulário acadêmico.
    candidate = bool(acts) and (
        course_hits >= 1 or context_hits >= 1
    ) and score >= 6

    # Rejeição explícita de falsos positivos óbvios.
    if exclude_hits and not re.search(r"\bSERES\b|secretaria de regulação e supervisão da educação superior|curso de graduação|curso superior", title + " " + text, re.I):
        candidate = False

    if candidate:
        reasons.extend([f"curso_hits:{course_hits}", f"contexto_hits:{context_hits}"])
    else:
        reasons.append(f"nao_candidato(score={score})")

    return {
        "candidate": candidate,
        "score": score,
        "act_types": sorted(set(acts)),
        "reasons": reasons,
        "course_hits": course_hits,
        "context_hits": context_hits,
        "exclude_hits": exclude_hits,
    }

def build_official_reference(item):
    # A URL existente no XML aponta para a página oficial da edição/PDF.
    # Não inventamos URL individual do DOU nesta fase.
    return item.get("pdfPage") or ""

def main():
    MONITORING.mkdir(parents=True, exist_ok=True)
    op = opener()
    login(op)

    all_items = []
    download_stats = []

    for date_iso in DATES:
        for section in SECTIONS:
            url = DOWNLOAD.format(date=date_iso, section=section)
            status, headers, final_url, blob = request(op, url, headers={"Referer":ACCESS}, timeout=240)

            stat = {
                "date": date_iso,
                "section": section,
                "url": url,
                "http_status": status,
                "final_url": final_url,
                "content_type": headers.get("Content-Type"),
                "body_size": len(blob),
                "is_zip": blob.startswith(b"PK"),
                "zip_entries": 0,
                "xml_entries": 0,
            }

            if not blob.startswith(b"PK"):
                stat["error"] = "Resposta não é ZIP."
                download_stats.append(stat)
                continue

            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".xml")]
                stat["zip_entries"] = len(z.namelist())
                stat["xml_entries"] = len(names)

                for name in names:
                    try:
                        item = article_data(z.read(name), name)
                        if item:
                            item["source_date"] = date_iso
                            item["source_section"] = section
                            item["source_reference"] = build_official_reference(item)
                            item["classification"] = classify(item)
                            all_items.append(item)
                    except Exception as exc:
                        # Mantém a execução robusta e registra apenas o arquivo problemático.
                        all_items.append({
                            "filename": name,
                            "source_date": date_iso,
                            "source_section": section,
                            "parse_error": str(exc)
                        })

            download_stats.append(stat)

    candidates = []
    for item in all_items:
        c = item.get("classification", {})
        if c.get("candidate"):
            # Não envia o corpo integral ao JSON para manter o artefato pequeno.
            candidates.append({
                "filename": item["filename"],
                "id": item.get("id"),
                "idMateria": item.get("idMateria"),
                "idOficio": item.get("idOficio"),
                "name": item.get("name"),
                "pubName": item.get("pubName"),
                "artType": item.get("artType"),
                "pubDate": item.get("pubDate"),
                "artCategory": item.get("artCategory"),
                "numberPage": item.get("numberPage"),
                "editionNumber": item.get("editionNumber"),
                "identifica": item.get("identifica"),
                "titulo": item.get("titulo"),
                "ementa": item.get("ementa"),
                "pdfPage": item.get("pdfPage"),
                "source_date": item.get("source_date"),
                "source_section": item.get("source_section"),
                "score": c.get("score"),
                "act_types": c.get("act_types"),
                "reasons": c.get("reasons"),
            })

    result = {
        "version":"V10.6",
        "executed_at":datetime.now(timezone.utc).isoformat(),
        "scope":{
            "dates":DATES,
            "sections":SECTIONS,
            "rule":"triagem de possíveis atos de autorização, reconhecimento, renovação de reconhecimento e aditamento relacionados a graduação",
            "confirmation":False
        },
        "summary":{
            "xmls_processados":len(all_items),
            "candidatos":len(candidates),
            "erros_parse":sum(1 for x in all_items if "parse_error" in x),
        },
        "downloads":download_stats,
        "candidates":candidates,
        "next_step":"Validar semanticamente os candidatos e extrair IES/curso/grau/modalidade antes de confirmar qualquer ato."
    }

    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({
        "version":"V10.6",
        "xmls_processados":result["summary"]["xmls_processados"],
        "candidatos":result["summary"]["candidatos"],
        "erros_parse":result["summary"]["erros_parse"],
        "output":str(OUT)
    },ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
