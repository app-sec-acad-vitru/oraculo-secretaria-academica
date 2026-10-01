#!/usr/bin/env python3
"""
Oráculo da Secretaria Acadêmica — V10.2.1
Diagnóstico da camada de download INLABS.

Objetivo:
- manter o login INLABS;
- diagnosticar exatamente o retorno do endpoint de download;
- registrar HTTP, Content-Type, tamanho, primeiros bytes e URL;
- não gravar conteúdo sensível;
- não confirmar nenhum ato nesta versão;
- preservar a política de confirmação pelo DOU individual.

Esta versão é diagnóstica: primeiro identifica o formato real retornado pelo
INLABS antes de alterar a rotina de ingestão XML.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from http.cookiejar import CookieJar

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "monitoring"
LOG_FILE = MONITORING / "atos_regulatorios_log.json"
OUT_FILE = MONITORING / "atos_regulatorios.json"

INLABS_LOGIN = "https://inlabs.in.gov.br/logar.php"
INLABS_ACCESS = "https://inlabs.in.gov.br/acessar.php"
INLABS_DOWNLOAD = "https://inlabs.in.gov.br/index.php?p={date}&dl={date}-{section}.zip"
INLABS_ORIGEM = "736372697074"

def http_session():
    jar = CookieJar()
    opener = build_opener()
    opener.add_handler(__import__("urllib.request", fromlist=["HTTPCookieProcessor"]).HTTPCookieProcessor(jar))
    return opener, jar

def request(opener, url, data=None, headers=None, timeout=60):
    h = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Referer": "https://inlabs.in.gov.br/",
    }
    if headers:
        h.update(headers)
    req = Request(
        url,
        data=data,
        headers=h,
        method="POST" if data is not None else "GET",
    )
    with opener.open(req, timeout=timeout) as r:
        body = r.read()
        return r.status, dict(r.headers.items()), body

def login(opener):
    email = os.getenv("INLABS_EMAIL", "").strip()
    password = os.getenv("INLABS_PASSWORD", "").strip()

    if not email or not password:
        return False, "Credenciais INLABS não configuradas."

    try:
        try:
            request(opener, INLABS_ACCESS, timeout=30)
        except Exception:
            pass

        payload = urlencode({
            "email": email,
            "password": password,
        }).encode()

        status, headers, body = request(
            opener,
            INLABS_LOGIN,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origem": INLABS_ORIGEM,
            },
            timeout=30,
        )

        text = body.decode("utf-8", "ignore")
        if status != 200:
            return False, f"Login HTTP {status}"

        if re.search(r"tente mais tarde|#\s*01", text, re.I):
            return False, "INLABS informou bloqueio temporário."

        return True, "Login INLABS realizado."

    except Exception as exc:
        return False, f"Falha no login: {exc}"

def diagnose_download(opener, date_iso, section):
    url = INLABS_DOWNLOAD.format(date=date_iso, section=section)

    result = {
        "date": date_iso,
        "section": section,
        "url": url,
        "http_status": None,
        "content_type": None,
        "content_length_header": None,
        "body_size": 0,
        "first_bytes_hex": "",
        "first_bytes_ascii": "",
        "is_zip_signature": False,
        "looks_like_html": False,
        "looks_like_json": False,
        "redirect": None,
        "error": None,
    }

    try:
        status, headers, body = request(opener, url, timeout=120)
        result["http_status"] = status
        result["content_type"] = headers.get("Content-Type")
        result["content_length_header"] = headers.get("Content-Length")
        result["body_size"] = len(body)

        first = body[:32]
        result["first_bytes_hex"] = first.hex()
        result["first_bytes_ascii"] = "".join(
            chr(b) if 32 <= b <= 126 else "." for b in first
        )

        result["is_zip_signature"] = body.startswith(b"PK")
        stripped = body.lstrip().lower()
        result["looks_like_html"] = (
            stripped.startswith(b"<!doctype html")
            or stripped.startswith(b"<html")
            or b"<html" in stripped[:500]
        )
        result["looks_like_json"] = stripped.startswith(b"{") or stripped.startswith(b"[")

        return result

    except Exception as exc:
        result["error"] = str(exc)
        return result

def main():
    MONITORING.mkdir(parents=True, exist_ok=True)

    opener, jar = http_session()
    ok, login_message = login(opener)

    diagnostics = []
    if ok:
        br_tz = timezone(timedelta(hours=-3))
        today = datetime.now(br_tz).date()

        # Testa hoje e ontem, DO1 e DO1E.
        for offset in range(2):
            d = today - timedelta(days=offset)
            date_iso = d.isoformat()

            for section in ("DO1", "DO1E"):
                diagnostics.append(
                    diagnose_download(opener, date_iso, section)
                )

    now = datetime.now(timezone.utc).isoformat()

    summary = {
        "version": "V10.2.1-DIAGNOSTICO",
        "executed_at": now,
        "login": {
            "status": "ok" if ok else "erro",
            "message": login_message,
        },
        "diagnostico_download": diagnostics,
        "interpretacao": {
            "PK": "ZIP válido/esperado",
            "HTML": "Resposta de página/erro/sessão em vez do ZIP",
            "JSON": "Resposta JSON/API em vez do ZIP",
            "outro": "Resposta diferente do esperado",
        },
        "proxima_etapa": "Ajustar o endpoint de download somente após identificar o retorno real.",
    }

    LOG_FILE.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Não altera a base de atos nesta etapa.
    if not OUT_FILE.exists():
        OUT_FILE.write_text(
            json.dumps({
                "version": "V10.2.1-DIAGNOSTICO",
                "total_atos": 0,
                "confirmados": 0,
                "pendentes": 0,
                "atos": []
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
