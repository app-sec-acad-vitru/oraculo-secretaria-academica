#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fallback diagnóstico do buscador público oficial do DOU.

Consulta a API usada pelo buscador oficial em in.gov.br/consulta.
Não baixa ZIP/XML, não classifica atos como confirmados e não altera o Radar.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.in.gov.br/consulta/-/buscar/dou"
ARTICLE_BASE = "https://www.in.gov.br/web/dou/-/"
OUTPUT = Path(__file__).resolve().parents[1] / "monitoring" / "dou_public_search_fallback.json"
TERMS = (
    "autorização de curso",
    "reconhecimento de curso",
    "renovação de reconhecimento de curso",
    "aditamento de curso",
)
SECTIONS = ("do1",)
DAYS_BACK = max(0, int(__import__("os").getenv("DOU_FALLBACK_DAYS_BACK", "6")))
TIMEOUT = int(__import__("os").getenv("DOU_FALLBACK_TIMEOUT", "25"))
USER_AGENT = "Mozilla/5.0 (compatible; OraculoSecretariaAcademica/1.0)"


def parse_results(html: bytes) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="_br_com_seatecnologia_in_buscadou_BuscaDouPortlet_params")
    if tag is None or not tag.string and not tag.contents:
        raise RuntimeError("Estrutura de resultados do buscador DOU não localizada.")
    raw = tag.string or tag.contents[0]
    payload = json.loads(raw)
    items = payload.get("jsonArray") or []
    results = []
    for item in items:
        url_title = str(item.get("urlTitle") or "").strip()
        results.append({
            "id": str(item.get("classPK") or ""),
            "title": str(item.get("title") or ""),
            "abstract": str(item.get("content") or ""),
            "date": str(item.get("pubDate") or ""),
            "section": str(item.get("pubName") or ""),
            "act_type": str(item.get("artType") or ""),
            "url": ARTICLE_BASE + url_title if url_title else "",
            "hierarchy": str(item.get("hierarchyStr") or ""),
        })
    return results


def search(term: str, start: datetime, end: datetime, section: str = "do1") -> list[dict]:
    params = {
        "q": term,
        "exactDate": "personalizado",
        "publishFrom": start.strftime("%d-%m-%Y"),
        "publishTo": end.strftime("%d-%m-%Y"),
        "sortType": "0",
        "s": section,
    }
    response = requests.get(
        BASE_URL,
        params=params,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Cache-Control": "no-cache",
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    results = parse_results(response.content)
    for item in results:
        item["query_term"] = term
    return results


def collect() -> dict:
    end = datetime.now(ZoneInfo("America/Sao_Paulo")).replace(hour=23, minute=59, second=59, microsecond=0)
    start = (end - timedelta(days=DAYS_BACK)).replace(hour=0, minute=0, second=0, microsecond=0)
    found: dict[str, dict] = {}
    errors = []
    queries = []
    for term in TERMS:
        try:
            items = search(term, start, end)
            queries.append({"term": term, "results": len(items), "status": "ok"})
            for item in items:
                key = item["id"] or item["url"] or (item["title"] + item["date"])
                if key:
                    found[key] = item
        except Exception as exc:
            queries.append({"term": term, "status": "erro"})
            errors.append({"term": term, "error": f"{type(exc).__name__}: {exc}"})
    results = sorted(found.values(), key=lambda x: (x["date"], x["title"]), reverse=True)
    return {
        "schema_version": 1,
        "collected_at": datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(),
        "source": "Buscador oficial do Diário Oficial da União — Imprensa Nacional",
        "endpoint": BASE_URL,
        "period": {"from": start.date().isoformat(), "to": end.date().isoformat()},
        "status": "consulta_concluida" if not errors else ("consulta_parcial" if results else "fonte_indisponivel"),
        "queries": queries,
        "results_count": len(results),
        "results": results,
        "errors": errors,
        "radar_modified": False,
        "candidate_acts_created": 0,
        "note": "Fallback diagnóstico: resultados de busca não são atos confirmados. Nenhum XML/ZIP foi baixado e o Radar não foi alterado.",
    }


def main() -> int:
    report = collect()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "results_count": report["results_count"],
        "errors": report["errors"],
        "output": str(OUTPUT),
        "radar_modified": False,
    }, ensure_ascii=False))
    return 0 if report["status"] in {"consulta_concluida", "consulta_parcial"} else 1


if __name__ == "__main__":
    sys.exit(main())
