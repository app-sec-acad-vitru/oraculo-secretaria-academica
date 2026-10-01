#!/usr/bin/env python3
"""
Oráculo da Secretaria Acadêmica — V10.5
Captura controlada de arquivos INLABS.

Objetivo desta versão:
- reutilizar a autenticação que já foi validada na V10.4;
- baixar somente os arquivos de 30/09/2026 e 29/09/2026;
- testar DO1 e DO1E;
- confirmar assinatura ZIP;
- abrir o ZIP em memória;
- contar XMLs e registrar amostras de nomes;
- inspecionar alguns XMLs para descobrir a estrutura real;
- NÃO criar/confirmar atos regulatórios.

A versão seguinte usará a estrutura real encontrada para construir o parser.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request, build_opener
from http.cookiejar import CookieJar
from html.parser import HTMLParser
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "monitoring"
LOG_FILE = MONITORING / "atos_regulatorios_log.json"
CAPTURE_FILE = MONITORING / "inlabs_capture_diagnostic.json"

BASE = "https://inlabs.in.gov.br"
ACCESS = f"{BASE}/acessar.php"
DOWNLOAD = f"{BASE}/index.php?p={{date}}&dl={{date}}-{{section}}.zip"
ORIGEM = "736372697074"

TARGET_DATES = ["2026-09-30", "2026-09-29"]
SECTIONS = ["DO1", "DO1E"]

class FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "form":
            self.current = {
                "action": a.get("action",""),
                "method": a.get("method","get").lower(),
                "inputs": []
            }
            self.forms.append(self.current)
        elif self.current is not None and tag.lower() == "input":
            self.current["inputs"].append(a)

    def handle_endtag(self, tag):
        if tag.lower() == "form":
            self.current = None

def make_opener():
    jar = CookieJar()
    op = build_opener()
    op.add_handler(__import__(
        "urllib.request",
        fromlist=["HTTPCookieProcessor"]
    ).HTTPCookieProcessor(jar))
    return op, jar

def request(op, url, data=None, headers=None, timeout=120):
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Connection": "keep-alive",
    }
    if headers:
        h.update(headers)
    req = Request(
        url,
        data=data,
        headers=h,
        method="POST" if data is not None else "GET"
    )
    with op.open(req, timeout=timeout) as r:
        return r.status, dict(r.headers.items()), r.geturl(), r.read()

def visible(body):
    s = body.decode("utf-8","ignore")
    s = re.sub(r"<script\b[^>]*>.*?</script>"," ",s,flags=re.I|re.S)
    s = re.sub(r"<style\b[^>]*>.*?</style>"," ",s,flags=re.I|re.S)
    s = re.sub(r"<[^>]+>"," ",s)
    s = unescape(s)
    return re.sub(r"\s+"," ",s).strip()

def login(op):
    email = os.getenv("INLABS_EMAIL","").strip()
    password = os.getenv("INLABS_PASSWORD","").strip()

    if not email or not password:
        return False, "Secrets INLABS não configurados."

    status, headers, final_url, body = request(op, ACCESS, timeout=30)

    parser = FormParser()
    parser.feed(body.decode("utf-8","ignore"))

    form = None
    for candidate in parser.forms:
        if candidate.get("action","").lower().endswith("logar.php"):
            form = candidate
            break

    if not form:
        return False, "Formulário logar.php não encontrado."

    payload = {}

    for item in form["inputs"]:
        name = item.get("name","")
        typ = item.get("type","text").lower()

        if not name:
            continue

        if typ in ("submit","button","file"):
            continue

        payload[name] = item.get("value","")

    payload["email"] = email
    payload["password"] = password

    action = urljoin(ACCESS, form.get("action") or "logar.php")

    status2, headers2, final2, body2 = request(
        op,
        action,
        data=urlencode(payload).encode(),
        headers={
            "Content-Type":"application/x-www-form-urlencoded",
            "Referer":ACCESS,
            "Origin":BASE,
            "Origem":ORIGEM,
        },
        timeout=30
    )

    text = visible(body2).lower()

    authenticated = (
        "acessar.php" not in final2.lower()
        and bool(re.search(r"minha conta|sair|logout", text))
    )

    if not authenticated:
        return False, "Sessão INLABS não foi validada."

    return True, "Sessão INLABS autenticada."

def inspect_xml(xml_bytes, filename):
    result = {
        "filename": filename,
        "size_bytes": len(xml_bytes),
        "root": "",
        "root_attributes": {},
        "article_count": 0,
        "sample_elements": [],
        "sample_text": "",
        "sample_attributes": {},
        "contains_course_terms": False,
        "contains_regulatory_terms": False,
    }

    try:
        root = ET.fromstring(xml_bytes)
        result["root"] = root.tag
        result["root_attributes"] = {
            str(k): str(v)[:300]
            for k,v in root.attrib.items()
        }

        articles = []
        for el in root.iter():
            local = el.tag.split("}")[-1].lower()
            if local == "article":
                articles.append(el)

        result["article_count"] = len(articles)

        elements = []
        for el in root.iter():
            local = el.tag.split("}")[-1]
            if local not in elements:
                elements.append(local)
            if len(elements) >= 40:
                break
        result["sample_elements"] = elements

        if articles:
            article = articles[0]
            result["sample_attributes"] = {
                str(k): str(v)[:500]
                for k,v in article.attrib.items()
            }

            text = " ".join(
                t.strip()
                for t in article.itertext()
                if t and t.strip()
            )
            text = re.sub(r"\s+"," ",text).strip()
            result["sample_text"] = text[:2500]

            lower = text.lower()
            result["contains_course_terms"] = bool(
                re.search(
                    r"curso|graduação|graduacao|bacharelado|licenciatura|tecnologia|tecnólogo|tecnologo",
                    lower
                )
            )
            result["contains_regulatory_terms"] = bool(
                re.search(
                    r"reconhecimento|autorização|autorizacao|aditamento|renovação|renovacao",
                    lower
                )
            )

    except Exception as exc:
        result["parse_error"] = str(exc)

    return result

def download_and_inspect(op, date_iso, section):
    url = DOWNLOAD.format(date=date_iso, section=section)

    result = {
        "date": date_iso,
        "section": section,
        "url": url,
        "http_status": None,
        "final_url": None,
        "content_type": None,
        "body_size": 0,
        "is_zip": False,
        "zip_entries": 0,
        "xml_entries": 0,
        "xml_total_bytes": 0,
        "sample_filenames": [],
        "xml_samples": [],
        "error": None,
    }

    try:
        status, headers, final_url, body = request(
            op,
            url,
            headers={"Referer":ACCESS},
            timeout=180
        )

        result["http_status"] = status
        result["final_url"] = final_url
        result["content_type"] = headers.get("Content-Type")
        result["body_size"] = len(body)
        result["is_zip"] = body.startswith(b"PK")

        if not result["is_zip"]:
            result["error"] = "Resposta não é ZIP."
            return result

        with zipfile.ZipFile(io.BytesIO(body)) as z:
            names = z.namelist()
            xml_names = [
                n for n in names
                if n.lower().endswith(".xml")
            ]

            result["zip_entries"] = len(names)
            result["xml_entries"] = len(xml_names)
            result["xml_total_bytes"] = sum(
                z.getinfo(n).file_size for n in xml_names
            )
            result["sample_filenames"] = xml_names[:20]

            # Inspeciona somente até 3 XMLs pequenos para não inflar o log.
            for name in xml_names[:3]:
                try:
                    data = z.read(name)
                    result["xml_samples"].append(
                        inspect_xml(data, name)
                    )
                except Exception as exc:
                    result["xml_samples"].append({
                        "filename": name,
                        "error": str(exc)
                    })

    except zipfile.BadZipFile:
        result["error"] = "ZIP inválido."
    except Exception as exc:
        result["error"] = str(exc)

    return result

def main():
    MONITORING.mkdir(parents=True, exist_ok=True)

    op, jar = make_opener()

    ok, login_message = login(op)

    output = {
        "version":"V10.5",
        "executed_at":datetime.now(timezone.utc).isoformat(),
        "login":{
            "status":"ok" if ok else "erro",
            "message":login_message
        },
        "capture":[],
        "important":"Nenhum ato é confirmado ou inserido nesta versão."
    }

    if ok:
        for date_iso in TARGET_DATES:
            for section in SECTIONS:
                output["capture"].append(
                    download_and_inspect(op,date_iso,section)
                )

    CAPTURE_FILE.write_text(
        json.dumps(output,ensure_ascii=False,indent=2),
        encoding="utf-8"
    )

    # Mantém o log principal simples.
    LOG_FILE.write_text(
        json.dumps(output,ensure_ascii=False,indent=2),
        encoding="utf-8"
    )

    print(json.dumps(output,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
