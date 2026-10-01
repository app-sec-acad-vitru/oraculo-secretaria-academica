#!/usr/bin/env python3
"""
Oráculo da Secretaria Acadêmica — V10.2.2
Diagnóstico avançado do retorno HTML do INLABS.

Não tenta confirmar atos nesta versão.
Objetivo: identificar, de forma segura, a mensagem/estrutura devolvida pelo
endpoint de download quando o INLABS responde HTTP 200 + HTML em vez de ZIP.

Nunca grava:
- senha;
- cookie;
- token;
- conteúdo integral da página.

Grava apenas metadados e trechos sanitizados de texto da página.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener
from http.cookiejar import CookieJar

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "monitoring"
LOG_FILE = MONITORING / "atos_regulatorios_log.json"

LOGIN = "https://inlabs.in.gov.br/logar.php"
ACCESS = "https://inlabs.in.gov.br/acessar.php"
DOWNLOAD = "https://inlabs.in.gov.br/index.php?p={date}&dl={date}-{section}.zip"
ORIGEM = "736372697074"

def opener():
    jar = CookieJar()
    op = build_opener()
    op.add_handler(__import__("urllib.request", fromlist=["HTTPCookieProcessor"]).HTTPCookieProcessor(jar))
    return op, jar

def request(op, url, data=None, headers=None, timeout=60):
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Referer": "https://inlabs.in.gov.br/",
        "Connection": "keep-alive",
    }
    if headers:
        h.update(headers)
    req = Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    with op.open(req, timeout=timeout) as r:
        return r.status, dict(r.headers.items()), r.geturl(), r.read()

def sanitize_html(body):
    text = body.decode("utf-8", "ignore")
    # Remove scripts/styles antes de extrair texto.
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<noscript\b[^>]*>.*?</noscript>", " ", text, flags=re.I | re.S)

    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()

    visible = re.sub(r"<[^>]+>", " ", text)
    visible = unescape(visible)
    visible = re.sub(r"\s+", " ", visible).strip()

    # Não expõe possíveis credenciais/token/cookies.
    visible = re.sub(r"(?i)(password|senha)\s*[:=]\s*\S+", r"\1=[REDACTED]", visible)
    visible = re.sub(r"(?i)(token|cookie|session|sess[aã]o)\s*[:=]\s*\S+", r"\1=[REDACTED]", visible)

    return title, visible[:2000]

def find_clues(body):
    text = body.decode("utf-8", "ignore")
    lower = text.lower()

    patterns = {
        "login": r"login|entrar|autentica",
        "senha": r"senha|password",
        "download": r"download|baixar|arquivo",
        "acesso_negado": r"acesso negado|acesso não autorizado|acesso nao autorizado|forbidden|unauthorized",
        "erro": r"erro|error|falha|failure",
        "sessao": r"session|sess[aã]o|cookie",
        "cloudflare": r"cloudflare|cf-ray|challenge",
        "captcha": r"captcha|recaptcha",
        "csrf": r"csrf|xsrf",
        "javascript": r"javascript|required",
    }
    return {k: bool(re.search(v, lower, re.I)) for k, v in patterns.items()}

def login(op):
    email = os.getenv("INLABS_EMAIL", "").strip()
    password = os.getenv("INLABS_PASSWORD", "").strip()

    if not email or not password:
        return False, "Credenciais não configuradas."

    try:
        # Acesso inicial para obter cookies/estado.
        try:
            request(op, ACCESS, timeout=30)
        except Exception:
            pass

        payload = urlencode({"email": email, "password": password}).encode()
        status, headers, final_url, body = request(
            op,
            LOGIN,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origem": ORIGEM,
                "Referer": ACCESS,
            },
            timeout=30,
        )

        if status != 200:
            return False, f"Login HTTP {status}"

        text = body.decode("utf-8", "ignore")
        if re.search(r"tente mais tarde|#\s*01", text, re.I):
            return False, "INLABS informou bloqueio temporário."

        return True, "Login INLABS realizado."

    except Exception as exc:
        return False, f"Falha no login: {exc}"

def diagnose(op, date_iso, section):
    url = DOWNLOAD.format(date=date_iso, section=section)

    result = {
        "date": date_iso,
        "section": section,
        "requested_url": url,
        "http_status": None,
        "final_url": None,
        "same_final_host": None,
        "content_type": None,
        "content_length_header": None,
        "body_size": 0,
        "zip_signature": False,
        "html_signature": False,
        "title": "",
        "visible_text_sample": "",
        "clues": {},
        "interesting_links": [],
        "interesting_forms": [],
        "error": None,
    }

    try:
        status, headers, final_url, body = request(op, url, timeout=120)
        result["http_status"] = status
        result["final_url"] = final_url
        result["same_final_host"] = (
            urlparse(final_url).netloc.lower() == urlparse(url).netloc.lower()
        )
        result["content_type"] = headers.get("Content-Type")
        result["content_length_header"] = headers.get("Content-Length")
        result["body_size"] = len(body)
        result["zip_signature"] = body.startswith(b"PK")
        result["html_signature"] = (
            body.lstrip().lower().startswith(b"<!doctype html")
            or body.lstrip().lower().startswith(b"<html")
        )

        title, sample = sanitize_html(body)
        result["title"] = title
        result["visible_text_sample"] = sample
        result["clues"] = find_clues(body)

        raw = body.decode("utf-8", "ignore")

        # Somente URLs/ações potencialmente úteis para diagnosticar fluxo.
        links = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw, re.I | re.S)
        for href, label in links[:50]:
            clean_label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(label))).strip()
            if re.search(r"login|acess|download|baix|arquivo|entrar|sair", clean_label, re.I) or \
               re.search(r"login|acess|download|baix|arquivo", href, re.I):
                result["interesting_links"].append({
                    "label": clean_label[:200],
                    "href": href[:500],
                })

        forms = re.findall(r"<form\b[^>]*?(?:action=[\"']([^\"']*)[\"'])?[^>]*>", raw, re.I)
        result["interesting_forms"] = [x[:500] for x in forms[:20]]

        return result

    except Exception as exc:
        result["error"] = str(exc)
        return result

def main():
    MONITORING.mkdir(parents=True, exist_ok=True)

    op, jar = opener()
    ok, message = login(op)

    diagnostics = []
    if ok:
        br = timezone(timedelta(hours=-3))
        today = datetime.now(br).date()

        for offset in range(2):
            d = today - timedelta(days=offset)
            for section in ("DO1", "DO1E"):
                diagnostics.append(diagnose(op, d.isoformat(), section))

    summary = {
        "version": "V10.2.2-DIAGNOSTICO-AVANCADO",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "login": {
            "status": "ok" if ok else "erro",
            "message": message,
        },
        "diagnostico_download": diagnostics,
        "seguranca": "Nenhuma senha, cookie ou token foi gravado.",
        "objetivo": "Identificar a página HTML devolvida pelo endpoint INLABS quando não entrega ZIP.",
        "proxima_etapa": "Ajustar a autenticação/endpoint de download com base nas pistas coletadas.",
    }

    LOG_FILE.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
