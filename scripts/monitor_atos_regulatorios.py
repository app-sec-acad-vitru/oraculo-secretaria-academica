#!/usr/bin/env python3
"""
Oráculo da Secretaria Acadêmica — V10.3
INLABS: autenticação baseada no formulário real + validação da sessão.

A V10.2 mostrou que o POST antigo recebia HTTP 200, mas não criava uma sessão
válida: qualquer download era redirecionado para acessar.php.

A V10.3:
1. abre acessar.php;
2. identifica dinamicamente o formulário de acesso;
3. coleta campos hidden;
4. identifica campos de login/senha por name/id;
5. envia o formulário para a action real;
6. verifica a sessão em acessar.php;
7. tenta o download somente se a sessão aparentar autenticada;
8. aceita ZIP apenas com assinatura PK;
9. mantém a confirmação oficial somente pelo DOU individual.

Se o portal mudar novamente os nomes dos campos, o log mostra os nomes
sanitizados encontrados, sem registrar credenciais.
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlencode, urlparse
from urllib.request import Request, build_opener
from http.cookiejar import CookieJar
from html.parser import HTMLParser
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "monitoring"
OUT_FILE = MONITORING / "atos_regulatorios.json"
LOG_FILE = MONITORING / "atos_regulatorios_log.json"
CANDIDATES_FILE = MONITORING / "dou_candidates.json"

BASE = "https://inlabs.in.gov.br"
ACCESS = f"{BASE}/acessar.php"
DOWNLOAD = f"{BASE}/index.php?p={{date}}&dl={{date}}-{{section}}.zip"

class FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None
        self.select_name = None
        self.select_values = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "form":
            self.current = {
                "action": a.get("action", ""),
                "method": a.get("method", "get").lower(),
                "inputs": [],
                "selects": [],
            }
            self.forms.append(self.current)
        elif self.current is not None and tag.lower() == "input":
            self.current["inputs"].append({
                "name": a.get("name", ""),
                "id": a.get("id", ""),
                "type": a.get("type", "text").lower(),
                "value": a.get("value", ""),
                "placeholder": a.get("placeholder", ""),
            })
        elif self.current is not None and tag.lower() == "select":
            self.select_name = a.get("name", "")
            self.select_values = []
        elif self.current is not None and tag.lower() == "option" and self.select_name:
            self.select_values.append({
                "value": a.get("value", "")
            })

    def handle_endtag(self, tag):
        if tag.lower() == "select" and self.current is not None and self.select_name:
            self.current["selects"].append({
                "name": self.select_name,
                "options": self.select_values[:]
            })
            self.select_name = None
            self.select_values = []
        elif tag.lower() == "form":
            self.current = None

def make_opener():
    jar = CookieJar()
    op = build_opener()
    op.add_handler(__import__("urllib.request", fromlist=["HTTPCookieProcessor"]).HTTPCookieProcessor(jar))
    return op, jar

def http(op, url, data=None, headers=None, timeout=60):
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Connection": "keep-alive",
    }
    if headers:
        h.update(headers)
    req = Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    with op.open(req, timeout=timeout) as r:
        return r.status, dict(r.headers.items()), r.geturl(), r.read()

def clean_text(body):
    s = body.decode("utf-8", "ignore")
    s = re.sub(r"<script\b[^>]*>.*?</script>", " ", s, flags=re.I|re.S)
    s = re.sub(r"<style\b[^>]*>.*?</style>", " ", s, flags=re.I|re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()

def parse_forms(body):
    p = FormParser()
    p.feed(body.decode("utf-8", "ignore"))
    return p.forms

def score_form(form):
    score = 0
    blob = json.dumps(form, ensure_ascii=False).lower()
    if "password" in blob or "senha" in blob:
        score += 5
    if "email" in blob or "login" in blob:
        score += 5
    if "acessar" in blob or "entrar" in blob:
        score += 2
    if form.get("method") == "post":
        score += 2
    return score

def identify_fields(form):
    login = None
    password = None

    for item in form.get("inputs", []):
        blob = " ".join([
            item.get("name",""),
            item.get("id",""),
            item.get("placeholder",""),
        ]).lower()
        typ = item.get("type","").lower()

        if not login and typ not in ("password", "hidden", "submit", "button"):
            if re.search(r"email|e-mail|login|usuario|usuário", blob):
                login = item.get("name") or item.get("id")

        if not password and typ == "password":
            password = item.get("name") or item.get("id")

        if not password and re.search(r"senha|password", blob):
            if typ not in ("hidden","submit","button"):
                password = item.get("name") or item.get("id")

    return login, password

def sanitize_form(form):
    return {
        "action": form.get("action",""),
        "method": form.get("method",""),
        "inputs": [
            {
                "name": x.get("name",""),
                "id": x.get("id",""),
                "type": x.get("type",""),
                "has_value": bool(x.get("value","")),
                "placeholder": x.get("placeholder","")[:80],
            }
            for x in form.get("inputs", [])
        ],
        "selects": [
            {"name": x.get("name",""), "option_count": len(x.get("options",[]))}
            for x in form.get("selects",[])
        ]
    }

def looks_authenticated(body, final_url):
    text = clean_text(body).lower()
    # Sinais fortes de sessão logada.
    positive = [
        "minha conta", "sair", "logout", "download",
        "dados abertos", "edições", "arquivos"
    ]
    negative = [
        "acessar e-mail", "esqueci a senha",
        "registrar e-mail", "repita a senha"
    ]
    pos = sum(1 for x in positive if x in text)
    neg = sum(1 for x in negative if x in text)
    if "/acessar.php" not in final_url.lower() and pos >= 1 and neg == 0:
        return True, {"positive": pos, "negative": neg}
    return False, {"positive": pos, "negative": neg}

def login_inlabs(op, diagnostics):
    email = os.getenv("INLABS_EMAIL", "").strip()
    password = os.getenv("INLABS_PASSWORD", "").strip()
    if not email or not password:
        return False, "Credenciais INLABS não configuradas."

    try:
        status, headers, final_url, body = http(op, ACCESS, timeout=30)
        forms = parse_forms(body)

        diagnostics["access_page"] = {
            "http_status": status,
            "final_url": final_url,
            "form_count": len(forms),
            "forms": [sanitize_form(f) for f in forms],
        }

        if not forms:
            return False, "Nenhum formulário encontrado em acessar.php."

        forms_sorted = sorted(forms, key=score_form, reverse=True)
        form = forms_sorted[0]
        login_field, password_field = identify_fields(form)

        diagnostics["selected_form"] = {
            "score": score_form(form),
            "login_field_found": bool(login_field),
            "password_field_found": bool(password_field),
        }

        if not login_field or not password_field:
            return False, "Não foi possível identificar os campos de login e senha."

        payload = {}
        for item in form.get("inputs", []):
            name = item.get("name")
            if not name:
                continue
            typ = item.get("type","text").lower()
            if typ in ("submit","button","file"):
                continue
            payload[name] = item.get("value","")

        payload[login_field] = email
        payload[password_field] = password

        # Alguns portais usam campo/valor no submit; se houver submit nomeado,
        # preservamos o primeiro valor.
        for item in form.get("inputs", []):
            if item.get("type") == "submit" and item.get("name"):
                payload[item["name"]] = item.get("value","")
                break

        action = urljoin(ACCESS, form.get("action") or "acessar.php")
        method = form.get("method","post").lower()

        if method != "post":
            # O login de portais desse tipo normalmente é POST. Não enviaremos
            # credenciais via URL caso o HTML tenha método GET inesperadamente.
            return False, f"Formulário de login usa método {method.upper()}, abortado por segurança."

        status2, headers2, final2, body2 = http(
            op,
            action,
            data=urlencode(payload).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": ACCESS,
                "Origin": BASE,
            },
            timeout=30,
        )

        diagnostics["login_response"] = {
            "http_status": status2,
            "final_url": final2,
            "content_type": headers2.get("Content-Type"),
            "body_size": len(body2),
            "suspicious_login_page": "acessar.php" in final2.lower() and
                                     "acessar e-mail" in clean_text(body2).lower(),
        }

        # Reabre a página de acesso com o mesmo CookieJar para validar a sessão.
        status3, headers3, final3, body3 = http(op, ACCESS, timeout=30)
        authenticated, evidence = looks_authenticated(body3, final3)

        diagnostics["session_check"] = {
            "http_status": status3,
            "final_url": final3,
            "authenticated": authenticated,
            "evidence": evidence,
        }

        if not authenticated:
            return False, "Login enviado, mas a sessão não foi validada em acessar.php."

        return True, "Sessão INLABS autenticada e validada."

    except Exception as exc:
        return False, f"Falha na autenticação INLABS: {exc}"

def download_zip(op, date_iso, section, diagnostics):
    url = DOWNLOAD.format(date=date_iso, section=section)
    status, headers, final_url, body = http(
        op,
        url,
        headers={"Referer": ACCESS},
        timeout=120,
    )

    item = {
        "date": date_iso,
        "section": section,
        "requested_url": url,
        "http_status": status,
        "final_url": final_url,
        "content_type": headers.get("Content-Type"),
        "body_size": len(body),
        "is_zip": body.startswith(b"PK"),
        "redirected_to_login": "acessar.php" in final_url.lower(),
        "error": None,
    }

    if not body.startswith(b"PK"):
        item["error"] = "Resposta não é ZIP."
        diagnostics.setdefault("downloads", []).append(item)
        return None, item

    diagnostics.setdefault("downloads", []).append(item)
    return body, item

def norm(s):
    s = unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def is_individual_dou(url):
    u = (url or "").lower()
    return (
        "in.gov.br/web/dou/-/" in u
        and not re.search(r"/web/dou/-/dou/\d{4}-\d{2}-\d{2}/secao-", u)
    )

def parse_article(xml_bytes, filename, date_iso, section):
    root = ET.fromstring(xml_bytes)
    def local(tag):
        return tag.split("}")[-1].lower()

    article = root
    for el in root.iter():
        if local(el.tag) == "article":
            article = el
            break

    attrs = {k.lower(): v for k,v in article.attrib.items()}

    def first(names):
        wanted = {x.lower() for x in names}
        for el in article.iter():
            if local(el.tag) in wanted:
                return norm("".join(el.itertext()))
        return ""

    title = first(["identifica","titulo","ementa"]) or filename
    url_title = attrs.get("name") or attrs.get("urltitle") or ""
    link = "https://www.in.gov.br/web/dou/-/" + url_title if url_title else ""

    text_el = next((e for e in article.iter() if local(e.tag) == "texto"), None)
    text = norm("".join(text_el.itertext()))[:30000] if text_el is not None else ""

    return {
        "title": norm(title),
        "ementa": first(["ementa"]),
        "orgao": norm(attrs.get("artcategory","") or first(["nomeorgao","orgao"])),
        "texto": text,
        "link": link,
        "published": attrs.get("pubdate","") or first(["pubdate","data"]) or date_iso,
        "arttype": attrs.get("arttype",""),
        "section": section,
        "date": date_iso,
    }

def collect_candidates(blob, date_iso, section):
    candidates = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for filename in z.namelist():
            if not filename.lower().endswith(".xml"):
                continue
            try:
                item = parse_article(z.read(filename), filename, date_iso, section)
                combined = norm(" ".join([
                    item["title"], item["ementa"], item["orgao"],
                    item["texto"], item["arttype"]
                ]))
                # Amplo o suficiente para não restringir a uma IES.
                if re.search(
                    r"reconhecimento|renova(?:ção|cao) de reconhecimento|autorização|autorizacao|aditamento",
                    combined, re.I
                ) and re.search(
                    r"curso|graduação|graduacao|bacharelado|licenciatura|tecnologia|tecnólogo|tecnologo",
                    combined, re.I
                ):
                    candidates.append(item)
            except Exception:
                continue
    return candidates

def load_base():
    if not OUT_FILE.exists():
        return {"version":"V10.3","total_atos":0,"confirmados":0,"pendentes":0,"atos":[]}
    try:
        return json.loads(OUT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"version":"V10.3","total_atos":0,"confirmados":0,"pendentes":0,"atos":[]}

def main():
    MONITORING.mkdir(parents=True, exist_ok=True)

    op, jar = make_opener()
    diag = {"version":"V10.3","executed_at":datetime.now(timezone.utc).isoformat()}
    ok, msg = login_inlabs(op, diag)
    diag["login"] = {"status":"ok" if ok else "erro","message":msg}

    all_candidates = []
    if ok:
        br = timezone(timedelta(hours=-3))
        today = datetime.now(br).date()

        for offset in range(2):
            d = today - timedelta(days=offset)
            date_iso = d.isoformat()
            for section in ("DO1","DO1E"):
                blob, info = download_zip(op, date_iso, section, diag)
                if blob:
                    try:
                        all_candidates.extend(
                            collect_candidates(blob, date_iso, section)
                        )
                    except Exception as exc:
                        info["error"] = f"ZIP/XML: {exc}"

    # Nesta versão, só gravamos candidatos se houver publicação individual DOU.
    # A confirmação de ato continua condicionada ao parser/validação posterior.
    unique = {}
    for c in all_candidates:
        if is_individual_dou(c.get("link","")):
            key = c["link"]
            unique[key] = c

    diag["candidates_received"] = len(unique)
    diag["source_policy"] = {
        "primary":"DOU/Imprensa Nacional",
        "ingestion":"INLABS XML",
        "secondary":"e-MEC",
        "scope":"Todos os cursos de graduação, sem restrição por IES, mantenedora, modalidade ou localidade.",
        "confirmation_rule":"Somente publicação individual do DOU com evidência suficiente."
    }

    # Mantém base anterior; nesta primeira V10.3 o objetivo é provar a ingestão.
    base = load_base()
    base["version"] = "V10.3"
    base["updated_at"] = datetime.now(timezone.utc).isoformat()
    OUT_FILE.write_text(json.dumps(base,ensure_ascii=False,indent=2),encoding="utf-8")

    LOG_FILE.write_text(
        json.dumps(diag,ensure_ascii=False,indent=2),
        encoding="utf-8"
    )

    print(json.dumps(diag,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
