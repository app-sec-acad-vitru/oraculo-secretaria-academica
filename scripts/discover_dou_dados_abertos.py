#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Descoberta não invasiva do catálogo oficial de Dados Abertos.

A API atual do dados.gov.br exige chave no cabeçalho Authorization.
Este script não altera o Radar, não baixa recursos e não cria candidatos.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_BASE = os.getenv(
    "DADOS_GOV_API",
    "https://dados.gov.br/dados/api/publico/conjuntos-dados",
)
OPENAPI_URL = "https://dados.gov.br/v3/api-docs"
TERMS = ("Diário Oficial da União", "Imprensa Nacional")
TIMEOUT = int(os.getenv("DADOS_GOV_TIMEOUT", "30"))
API_TOKEN = os.getenv("DADOS_GOV_API_TOKEN", "").strip()
OUTPUT = Path(__file__).resolve().parents[1] / "monitoring" / "dou_dados_abertos_discovery.json"


def query_catalog(term: str, token: str) -> list:
    params = urllib.parse.urlencode({
        "nomeConjuntoDados": term,
        "dadosAbertos": "true",
        "isPrivado": "false",
        "pagina": 1,
    })
    request = urllib.request.Request(
        API_BASE + "?" + params,
        headers={
            "User-Agent": "OraculoSecretariaAcademica/1.0",
            "Accept": "application/json",
            "Authorization": token,
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        if response.status != 200:
            raise RuntimeError(f"Catálogo respondeu HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("Formato inesperado: API atual deveria retornar uma lista JSON.")
    return payload


def summarize_dataset(dataset: dict) -> dict:
    resources = []
    raw_resources = dataset.get("recursos") or dataset.get("resources") or []
    for resource in raw_resources:
        if not isinstance(resource, dict):
            continue
        fmt = str(resource.get("formato") or resource.get("format") or "").upper()
        url = str(resource.get("link") or resource.get("url") or "")
        name = str(resource.get("titulo") or resource.get("name") or resource.get("descricao") or "")
        if fmt in {"XML", "ZIP", "GZ", "7Z"} or url.lower().split("?")[0].endswith((".xml", ".zip", ".xml.gz")):
            resources.append({
                "id": resource.get("id"),
                "name": name,
                "format": fmt,
                "url": url,
            })
    return {
        "id": dataset.get("id"),
        "name": dataset.get("nome") or dataset.get("name"),
        "title": dataset.get("titulo") or dataset.get("title"),
        "organization": dataset.get("organizacao") or dataset.get("organization"),
        "resources_count": len(raw_resources),
        "xml_or_archive_resources": resources,
    }


def discover(token: str | None = None) -> dict:
    token = (token if token is not None else API_TOKEN).strip()
    results = {}
    errors = []
    if not token:
        return {
            "schema_version": 2,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "source": "Portal Brasileiro de Dados Abertos — API REST oficial",
            "api_base": API_BASE,
            "status": "autenticacao_necessaria",
            "message": "A especificação OpenAPI oficial exige chave no cabeçalho Authorization.",
            "queries": {},
            "datasets": [],
            "datasets_count": 0,
            "xml_or_archive_resources_count": 0,
            "errors": [],
            "radar_modified": False,
            "downloads_performed": 0,
            "candidate_acts_created": 0,
        }

    unique = {}
    for term in TERMS:
        try:
            matches = query_catalog(term, token)
            results[term] = [summarize_dataset(item) for item in matches if isinstance(item, dict)]
            for item in results[term]:
                if item.get("id"):
                    unique[item["id"]] = item
        except urllib.error.HTTPError as exc:
            detail = "A chave foi recusada ou não foi aceita." if exc.code in (401, 403) else str(exc)
            errors.append({"query": term, "http_status": exc.code, "error": detail})
            results[term] = []
        except Exception as exc:
            errors.append({"query": term, "error": f"{type(exc).__name__}: {exc}"})
            results[term] = []

    resources = [r for ds in unique.values() for r in ds["xml_or_archive_resources"]]
    status = "catalogo_consultado" if unique and not errors else (
        "catalogo_parcial" if unique else ("autenticacao_rejeitada" if any(e.get("http_status") in (401, 403) for e in errors)
        else ("fonte_indisponivel" if errors else "nenhum_conjunto_localizado"))
    )
    return {
        "schema_version": 2,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source": "Portal Brasileiro de Dados Abertos — API REST oficial",
        "api_base": API_BASE,
        "status": status,
        "queries": results,
        "datasets": list(unique.values()),
        "datasets_count": len(unique),
        "xml_or_archive_resources_count": len(resources),
        "errors": errors,
        "radar_modified": False,
        "downloads_performed": 0,
        "candidate_acts_created": 0,
    }


def main() -> int:
    report = discover()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "datasets_count": report["datasets_count"],
        "xml_or_archive_resources_count": report["xml_or_archive_resources_count"],
        "errors": report["errors"],
        "output": str(OUTPUT),
        "radar_modified": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
