#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Triagem conservadora dos resultados do buscador público do DOU.

A triagem apenas organiza candidatos para revisão humana. Nunca confirma atos
nem altera monitoring/atos_regulatorios.json.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "monitoring" / "dou_public_search_fallback.json"
OUTPUT = ROOT / "monitoring" / "dou_public_search_triage.json"

# A origem institucional tem peso maior que palavras isoladas como "curso".
EDUCATION_AUTHORITIES = (
    "ministério da educação", "ministerio da educacao",
    "secretaria de regulação e supervisão da educação superior",
    "secretaria de regulacao e supervisao da educacao superior",
    "seres", "conselho nacional de educação", "conselho nacional de educacao",
    "câmara de educação superior", "camara de educacao superior",
    "instituto nacional de estudos e pesquisas educacionais", "inep",
)
ACADEMIC_TERMS = (
    "educação superior", "educacao superior", "instituição de educação superior",
    "instituicao de educacao superior", "ies", "graduação", "graduacao",
    "curso superior", "curso de graduação", "curso de graduacao",
    "reconhecimento de curso", "autorização de curso", "autorizacao de curso",
    "renovação de reconhecimento", "renovacao de reconhecimento",
    "diploma digital", "censo da educação superior", "censo da educacao superior",
    "enade", "diretrizes curriculares", "credenciamento de instituição",
    "credenciamento de instituicao", "recredenciamento",
)
EXPLICIT_REGULATORY_TERMS = (
    "autorização", "autorizacao", "reconhecimento", "renovação",
    "renovacao", "credenciamento", "recredenciamento", "diretrizes curriculares",
    "diploma", "ena(de)", "censo da educação superior", "censo da educacao superior",
)


def normalize(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\\s+", " ", value).strip().lower()


def classify(item: dict) -> dict:
    hierarchy = normalize(item.get("hierarchy", ""))
    title = normalize(item.get("title", ""))
    abstract = normalize(item.get("abstract", ""))
    text = " ".join((title, abstract, hierarchy))
    authority_hits = [term for term in EDUCATION_AUTHORITIES if term in hierarchy or term in title]
    academic_hits = [term for term in ACADEMIC_TERMS if term in text]
    regulatory_hits = [term for term in EXPLICIT_REGULATORY_TERMS if term in text]

    # Ato emitido por autoridade educacional é encaminhado para revisão mesmo
    # quando o resumo não traz todas as palavras-chave.
    if authority_hits and (academic_hits or regulatory_hits):
        status = "candidato_relevante"
        reason = "Autoridade educacional identificada e assunto regulatório compatível."
    elif authority_hits:
        status = "revisao_manual"
        reason = "Autoridade educacional identificada; assunto precisa de conferência."
    elif academic_hits and regulatory_hits and any(
        marker in text for marker in ("mec", "seres", "cne", "inep", "educação superior", "educacao superior")
    ):
        status = "revisao_manual"
        reason = "Assunto acadêmico identificado, mas a autoridade emissora não foi confirmada."
    else:
        status = "fora_do_escopo"
        reason = "Não há evidência suficiente de vínculo com regulação da educação superior."

    return {
        **item,
        "triage": {
            "status": status,
            "reason": reason,
            "authority_matches": authority_hits,
            "academic_matches": academic_hits,
            "regulatory_matches": regulatory_hits,
            "requires_human_validation": status != "fora_do_escopo",
            "confirmed": False,
        },
    }


def build_report(source: dict) -> dict:
    items = [classify(item) for item in source.get("results", [])]
    relevant = [x for x in items if x["triage"]["status"] == "candidato_relevante"]
    manual = [x for x in items if x["triage"]["status"] == "revisao_manual"]
    excluded = [x for x in items if x["triage"]["status"] == "fora_do_escopo"]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(),
        "source_file": "monitoring/dou_public_search_fallback.json",
        "source_status": source.get("status", "desconhecido"),
        "period": source.get("period", {}),
        "results_count": len(items),
        "counts": {
            "candidato_relevante": len(relevant),
            "revisao_manual": len(manual),
            "fora_do_escopo": len(excluded),
        },
        "results": items,
        "radar_modified": False,
        "acts_confirmed": 0,
        "note": "Triagem automatizada preliminar. Candidatos exigem validação humana e conferência do inteiro teor no DOU. Nenhum resultado foi integrado automaticamente ao Radar.",
    }


def main() -> int:
    if not INPUT.exists():
        print(f"Arquivo de entrada não encontrado: {INPUT}", file=sys.stderr)
        return 2
    source = json.loads(INPUT.read_text(encoding="utf-8"))
    report = build_report(source)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    print(json.dumps({"status": "triagem_concluida", "counts": report["counts"],
                      "output": str(OUTPUT), "radar_modified": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
