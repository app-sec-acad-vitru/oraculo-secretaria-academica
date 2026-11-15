#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Descoberta não invasiva de conjuntos do DOU no Portal Brasileiro de Dados Abertos.

Não baixa arquivos, não executa parser e não altera o Radar.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_BASE = os.getenv("DADOS_GOV_API", "https://dados.gov.br/api/3/action/package_search")
TERMS = ("Diário Oficial da União", "DOU Imprensa Nacional")
TIMEOUT = int(os.getenv("DADOS_GOV_TIMEOUT", "30"))
OUTPUT = Path(__file__).resolve().parents[1] / "monitoring" / "dou_dados_abertos_discovery.json"


def query_catalog(term: str) -> dict:
    url = API_BASE + "?" + urllib.parse.urlencode({"q": term, "rows": 50})
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "OraculoSecretariaAcademica/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        if response.status != 200:
            raise RuntimeError(f"Catálogo respondeu HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise RuntimeError("Resposta do catálogo não confirmou sucesso CKAN.")
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise RuntimeError("Formato inesperado no campo result do catálogo.")
    return result


def summarize_dataset(dataset: dict) -> dict:
    resources = []
    for resource in dataset.get("resources") or []:
        if not isinstance(resource, dict):
            continue
        fmt = str(resource.get("format") or "").upper()
        url = str(resource.get("url") or "")
        name = str(resource.get("name") or resource.get("description") or "")
        if fmt in {"XML", "ZIP", "GZ", "7Z"} or url.lower().endswith((".xml", ".zip", ".xml.gz")):
            resources.append({
                "id": resource.get("id"),
                "name": name,
                "format": fmt,
                "url": url,
                "created": resource.get("created"),
                "last_modified": resource.get("last_modified"),
                "size": resource.get("size"),
            })
    return {
        "id": dataset.get("id"),
        "name": dataset.get("name"),
        "title": dataset.get("title"),
        "organization": (dataset.get("organization") or {}).get("title"),
        "metadata_modified": dataset.get("metadata_modified"),
        "resources_count": len(dataset.get("resources") or []),
        "xml_or_archive_resources": resources,
        "dataset_url": "https://dados.gov.br/dados/conjuntos/" + str(dataset.get("name") or ""),
    }


def discover() -> dict:
    results = {}
    errors = []
    for term in TERMS:
        try:
            catalog = query_catalog(term)
            datasets = catalog.get("results") or []
            results[term] = [summarize_dataset(item) for item in datasets if isinstance(item, dict)]
        except Exception as exc:  # diagnóstico deve sobreviver à indisponibilidade de uma consulta
            results[term] = []
            errors.append({"query": term, "error": f"{type(exc).__name__}: {exc}"})

    unique = {}
    for matches in results.values():
        for item in matches:
            if item.get("id"):
                unique[item["id"]] = item

    resources = [r for ds in unique.values() for r in ds["xml_or_archive_resources"]]
    status = "catalogo_consultado" if unique and not errors else (
        "catalogo_parcial" if unique else ("fonte_indisponivel" if errors else "nenhum_conjunto_localizado")
    )
    return {
        "schema_version": 1,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source": "Portal Brasileiro de Dados Abertos — API CKAN",
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
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "datasets_count": report["datasets_count"],
        "xml_or_archive_resources_count": report["xml_or_archive_resources_count"],
        "errors": report["errors"],
        "output": str(OUTPUT),
        "radar_modified": False,
    }, ensure_ascii=False))
    # Descoberta é diagnóstica: indisponibilidade do catálogo não deve interromper o monitor oficial.
    return 0


if __name__ == "__main__":
    sys.exit(main())
