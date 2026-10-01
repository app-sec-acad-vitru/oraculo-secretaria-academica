#!/usr/bin/env python3
"""
Oráculo da Secretaria Acadêmica — V10.4
INLABS: reprodução fiel do POST de logar.php + diagnóstico da resposta.

A V10.3 confirmou:
- formulário de login: POST logar.php
- campos: email/password
- não há sessão autenticada após o POST

A V10.4 mantém o CookieJar e envia:
- todos os campos do formulário de login;
- valor do botão submit, quando existir;
- header Origem usado pelo fluxo conhecido do INLABS;
- Referer/Origin;
- Accept/Accept-Language;
- sem expor credenciais.

Além disso, registra somente sinais sanitizados da resposta de logar.php,
incluindo título, mensagens e redirecionamento.

Se a sessão continuar inválida, o log permitirá identificar a mensagem exata
do portal sem gravar senha/cookie/token.

Nenhum ato regulatório é confirmado nesta versão.
"""

from __future__ import annotations
import json, os, re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, build_opener
from http.cookiejar import CookieJar
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "monitoring"
LOG_FILE = MONITORING / "atos_regulatorios_log.json"

BASE = "https://inlabs.in.gov.br"
ACCESS = f"{BASE}/acessar.php"
LOGIN = f"{BASE}/logar.php"
ORIGEM = "736372697074"

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
    op.add_handler(__import__("urllib.request", fromlist=["HTTPCookieProcessor"]).HTTPCookieProcessor(jar))
    return op, jar

def request(op, url, data=None, headers=None, timeout=60):
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

def visible(body):
    s = body.decode("utf-8","ignore")
    s = re.sub(r"<script\b[^>]*>.*?</script>"," ",s,flags=re.I|re.S)
    s = re.sub(r"<style\b[^>]*>.*?</style>"," ",s,flags=re.I|re.S)
    s = re.sub(r"<[^>]+>"," ",s)
    s = unescape(s)
    s = re.sub(r"\s+"," ",s).strip()
    # Defesa adicional contra vazamento acidental.
    s = re.sub(r"(?i)(password|senha)\s*[:=]\s*\S+", r"\1=[REDACTED]", s)
    s = re.sub(r"(?i)(token|cookie|session|sess[aã]o)\s*[:=]\s*\S+", r"\1=[REDACTED]", s)
    return s

def title(body):
    s = body.decode("utf-8","ignore")
    m = re.search(r"<title[^>]*>(.*?)</title>",s,flags=re.I|re.S)
    if not m:
        return ""
    return re.sub(r"\s+"," ",unescape(re.sub(r"<[^>]+>"," ",m.group(1)))).strip()

def sanitize_form(form):
    return {
        "action": form.get("action",""),
        "method": form.get("method",""),
        "inputs": [
            {
                "name": x.get("name",""),
                "id": x.get("id",""),
                "type": x.get("type",""),
                "value_present": bool(x.get("value",""))
            }
            for x in form.get("inputs",[])
        ]
    }

def find_login_form(forms):
    candidates = []
    for f in forms:
        blob = json.dumps(f,ensure_ascii=False).lower()
        score = 0
        if f.get("action","").lower().endswith("logar.php"):
            score += 10
        if "password" in blob:
            score += 5
        if "email" in blob:
            score += 5
        if f.get("method") == "post":
            score += 2
        candidates.append((score,f))
    return max(candidates,key=lambda x:x[0])[1] if candidates else None

def login(op, diag):
    email = os.getenv("INLABS_EMAIL","").strip()
    password = os.getenv("INLABS_PASSWORD","").strip()
    if not email or not password:
        return False, "Secrets INLABS não configurados."

    status, headers, final_url, body = request(op, ACCESS, timeout=30)
    forms = FormParser()
    forms.feed(body.decode("utf-8","ignore"))
    form = find_login_form(forms.forms)

    diag["access_page"] = {
        "http_status": status,
        "final_url": final_url,
        "form_count": len(forms.forms),
        "selected_form": sanitize_form(form) if form else None
    }

    if not form:
        return False, "Formulário de login não encontrado."

    payload = {}
    submit = None
    for item in form["inputs"]:
        name = item.get("name","")
        typ = item.get("type","text").lower()
        if typ == "submit":
            # O botão do INLABS pode ser sem name. Se tiver name, preservar.
            if name:
                submit = (name,item.get("value",""))
            continue
        if name:
            payload[name] = item.get("value","")

    # Campos reais confirmados na V10.3.
    login_field = next((x.get("name") for x in form["inputs"]
                        if x.get("name") == "email"), "email")
    pass_field = next((x.get("name") for x in form["inputs"]
                       if x.get("name") == "password"), "password")
    payload[login_field] = email
    payload[pass_field] = password

    if submit:
        payload[submit[0]] = submit[1]

    action = urljoin(ACCESS, form.get("action") or "logar.php")
    if urlparse(action).netloc.lower() != urlparse(BASE).netloc.lower():
        return False, "Action do formulário aponta para host externo; abortado."

    # Reprodução do cabeçalho Origem utilizado no fluxo legado conhecido.
    request_headers = {
        "Content-Type":"application/x-www-form-urlencoded",
        "Referer": ACCESS,
        "Origin": BASE,
        "Origem": ORIGEM,
        "Cache-Control":"no-cache",
    }

    status2, headers2, final2, body2 = request(
        op, action,
        data=urlencode(payload).encode(),
        headers=request_headers,
        timeout=30
    )

    text2 = visible(body2)
    diag["login_post"] = {
        "action": action,
        "http_status": status2,
        "final_url": final2,
        "content_type": headers2.get("Content-Type"),
        "body_size": len(body2),
        "title": title(body2),
        "visible_text_sample": text2[:2500],
        "redirected_to_access": "acessar.php" in final2.lower(),
        "response_hints": {
            "invalid_credentials": bool(re.search(
                r"senha.*(incorreta|inv[aá]lida)|login.*(incorreto|inv[aá]lido)|usu[aá]rio.*(incorreto|inv[aá]lido)|n[aã]o.*autentic",
                text2,re.I)),
            "login_success": bool(re.search(
                r"minha conta|sair|logout|bem vindo|bem-vindo",
                text2,re.I)),
            "blocked": bool(re.search(
                r"bloquead|tente novamente|muitas tentativas|acesso negado|forbidden",
                text2,re.I))
        }
    }

    # Teste protegido: acessar a raiz/listagem após login.
    status3, headers3, final3, body3 = request(op, BASE + "/", timeout=30)
    text3 = visible(body3)
    authenticated = (
        "acessar.php" not in final3.lower()
        and bool(re.search(r"minha conta|sair|logout|download|dados abertos",text3,re.I))
    )

    diag["protected_check"] = {
        "http_status": status3,
        "final_url": final3,
        "content_type": headers3.get("Content-Type"),
        "authenticated": authenticated,
        "title": title(body3),
        "visible_text_sample": text3[:1200]
    }

    return authenticated, (
        "Sessão autenticada."
        if authenticated
        else "POST executado, mas a sessão protegida ainda não foi validada."
    )

def main():
    MONITORING.mkdir(parents=True,exist_ok=True)
    op, jar = make_opener()

    diag = {
        "version":"V10.4",
        "executed_at":datetime.now(timezone.utc).isoformat(),
        "security":"Nenhuma senha, cookie ou token é gravado."
    }

    ok,msg = login(op,diag)
    diag["login"] = {"status":"ok" if ok else "erro","message":msg}

    # Nesta versão não processamos atos. O objetivo é fechar autenticação.
    diag["candidates_received"] = 0
    diag["source_policy"] = {
        "primary":"DOU/Imprensa Nacional",
        "ingestion":"INLABS XML",
        "secondary":"e-MEC",
        "confirmation_rule":"Publicação individual do DOU + evidência suficiente."
    }

    LOG_FILE.write_text(json.dumps(diag,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(diag,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
