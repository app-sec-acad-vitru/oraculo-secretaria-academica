#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Oráculo da Secretaria Acadêmica — V10.8
Captura e triagem universal de atos regulatórios de cursos.

V10.8 mantém a lógica operacional da V10.7 (INLABS -> ZIP -> XML),
mas endurece a classificação para reduzir falsos positivos e cria
uma fila de validação para casos ambíguos.

Não altera monitoring/atos_regulatorios.json.
Saídas:
  monitoring/atos_validacao_v10_8.json
  monitoring/atos_validacao_v10_8.md
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import zipfile
import unicodedata
import html as html_lib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import requests


ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "monitoring"

EMAIL = os.getenv("INLABS_EMAIL", "").strip()
PASSWORD = os.getenv("INLABS_PASSWORD", "").strip()

SECTIONS = ("DO1", "DO1E")
DAYS_BACK = 1

LOGIN_URL = "https://inlabs.in.gov.br/logar.php"
ACCESS_URL = "https://inlabs.in.gov.br/acessar.php"
DOWNLOAD_BASE = "https://inlabs.in.gov.br/index.php?p="

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/154.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REGULATORY_TYPES = {
    "AUTORIZAÇÃO DE CURSO",
    "RECONHECIMENTO DE CURSO",
    "RENOVAÇÃO DE RECONHECIMENTO DE CURSO",
    "ADITAMENTO DE CURSO",
}

COURSE_STRONG = [
    "curso superior de graduação",
    "curso de graduação",
    "curso superior",
    "curso de bacharelado",
    "curso de licenciatura",
    "curso superior de tecnologia",
    "bacharelado em",
    "licenciatura em",
    "tecnologia em",
    "curso de tecnologia",
]

COURSE_CONTEXT = [
    "e-mec", "emec", "vagas totais anuais", "vagas",
    "carga horária total", "carga horária", "turno",
    "mantida pela", "mantida por", "mantenedora",
    "ofertado pela", "ofertada pela",
    "modalidade", "presencial", "educação a distância",
    "educacao a distancia",
]

COURSE_DEGREE = ["bacharelado", "licenciatura", "tecnologia", "tecnólogo"]

INSTITUTIONAL_TERMS = [
    "estatuto", "regimento", "recredenciamento institucional",
    "credenciamento institucional", "credenciamento da instituição",
    "recredenciamento da instituição",
    "instituição de educação superior",
    "instituicao de educacao superior",
]

CAMPUS_TERMS = [
    "campus", "unidade de ensino", "nova unidade de ensino",
    "funcionamento de novas unidades de ensino",
    "polo de apoio presencial",
]

PROCEDURAL_TERMS = [
    "processo seletivo", "concurso público", "concurso publico",
    "licitação", "licitacao", "tomada de contas", "pauta de julgamento",
]

TYPE_PATTERNS = [
    ("RENOVAÇÃO DE RECONHECIMENTO DE CURSO", [
        r"\brenova(?:ção|cao)\s+(?:do|de)\s+reconhecimento(?:\s+do|\s+de)?\s+curso\b",
        r"\brenova(?:ção|cao)\s+de\s+reconhecimento\s+de\s+curso\b",
    ]),
    ("RECONHECIMENTO DE CURSO", [
        r"\breconhece\s+o\s+curso\b",
        r"\breconhecimento\s+do\s+curso\b",
        r"\breconhecimento\s+de\s+curso\b",
    ]),
    ("AUTORIZAÇÃO DE CURSO", [
        r"\bautoriza\s+(?:o|a)\s+(?:funcionamento|oferta)\s+do\s+curso\b",
        r"\bautoriza\s+(?:o|a)\s+funcionamento\s+de\s+curso\b",
        r"\bautoriza(?:ção|cao)\s+(?:de|do|da)\s+curso\b",
    ]),
    ("ADITAMENTO DE CURSO", [
        r"\baditamento\s+(?:do|de)\s+curso\b",
        r"\baditamento.*\bcurso\b",
        r"\baltera(?:ção|cao).{0,100}\bcurso\b",
    ]),
]

EFFECT_PATTERNS = [
    ("renova", [r"\brenova(?:ção|cao)\b"]),
    ("reconhece", [r"\breconhece\b", r"\breconhecimento\b"]),
    ("autoriza", [r"\bautoriza\b", r"\bautorização\b", r"\bautorizacao\b"]),
    ("adita", [r"\baditamento\b", r"\badita\b"]),
    ("indefere", [r"\bindefere\b", r"\bindeferimento\b"]),
    ("anula", [r"\banula\b", r"\banulação\b", r"\banulacao\b"]),
    ("torna sem efeito", [r"\btorna\s+sem\s+efeito\b"]),
    ("restabelece", [r"\brestabelece\b", r"\brestabelecimento\b"]),
]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = html_lib.unescape(s).lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def clean_text(s: str) -> str:
    s = html_lib.unescape(s or "")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def parse_date_list():
    now = datetime.now(timezone.utc).date()
    return [now - timedelta(days=i) for i in range(DAYS_BACK + 1)]


def login(session: requests.Session):
    if not EMAIL or not PASSWORD:
        raise RuntimeError("INLABS_EMAIL/INLABS_PASSWORD não configurados.")

    session.headers.update(HEADERS)
    session.get(ACCESS_URL, timeout=60)

    payload = {
        "email": EMAIL,
        "password": PASSWORD,
        "Origem": "736372697074",
    }

    r = session.post(
        LOGIN_URL,
        data=payload,
        headers={
            "Referer": ACCESS_URL,
            "Origin": "https://inlabs.in.gov.br",
        },
        allow_redirects=True,
        timeout=60,
    )

    n = norm(r.text or "")
    if "este endereço de email não existe" in n or "senha incorreta" in n:
        raise RuntimeError("Credenciais INLABS rejeitadas.")

    if "sair" not in n or "minha conta" not in n:
        raise RuntimeError(
            f"Login INLABS não confirmado. final_url={r.url} status={r.status_code}"
        )

    return r


def download_zip(session: requests.Session, date_obj, section: str):
    date_str = date_obj.strftime("%Y-%m-%d")
    url = f"{DOWNLOAD_BASE}{date_str}&dl={date_str}-{section}.zip"

    r = session.get(
        url,
        headers={"Referer": "https://inlabs.in.gov.br/index.php?p="},
        allow_redirects=True,
        timeout=180,
    )

    content = r.content or b""
    is_zip = zipfile.is_zipfile(io.BytesIO(content))

    return {
        "date": date_str,
        "section": section,
        "requested_url": url,
        "final_url": r.url,
        "http_status": r.status_code,
        "content_type": r.headers.get("Content-Type", ""),
        "bytes": len(content),
        "is_zip": is_zip,
        "content": content if is_zip else None,
    }


def xml_to_text(root):
    return clean_text(" ".join(root.itertext()))


def extract_article(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    attrs = dict(root.attrib)

    article = None
    for el in root.iter():
        if el.tag.split("}")[-1].lower() == "article":
            article = el
            break
    article = article or root

    a = dict(article.attrib)

    meta = {
        "id": a.get("id") or attrs.get("id"),
        "name": clean_text(a.get("name") or ""),
        "artType": clean_text(a.get("artType") or ""),
        "pubName": clean_text(a.get("pubName") or ""),
        "pubDate": clean_text(a.get("pubDate") or ""),
        "editionNumber": clean_text(a.get("editionNumber") or ""),
        "numberPage": clean_text(a.get("numberPage") or ""),
        "idMateria": clean_text(a.get("idMateria") or ""),
        "idOficio": clean_text(a.get("idOficio") or ""),
        "urltitle": clean_text(a.get("urltitle") or ""),
        "url": clean_text(a.get("url") or ""),
    }

    parts = {}
    for el in article.iter():
        tag = el.tag.split("}")[-1]
        if tag in {"Identifica", "Data", "Ementa", "Titulo", "SubTitulo", "Texto", "Midias"}:
            parts[tag] = clean_text(" ".join(el.itertext()))

    return meta, parts, xml_to_text(article)


def parse_act_number_year(meta, parts):
    source = " ".join([
        meta.get("name", ""),
        parts.get("Identifica", ""),
        parts.get("Titulo", ""),
        parts.get("SubTitulo", ""),
    ])

    patterns = [
        r"(?:N[º°o]\s*)?([0-9]{1,6}(?:[-/][A-Z0-9]+)?)\s*,?\s*DE\s+"
        r"\d{1,2}\s+DE\s+[A-ZÇÃÕÉÊÍÓÚ]+\s+DE\s+(\d{4})",
        r"N[º°o]\s*([0-9]{1,6}).{0,120}?(\d{4})",
    ]

    for pat in patterns:
        m = re.search(pat, source, re.I)
        if m:
            return m.group(1), int(m.group(2))

    return "", None


def title_is_non_operational(title_norm: str):
    return any(x in title_norm for x in [
        "sumula", "súmula", "parecer", "nota tecnica", "nota técnica",
        "despacho", "extrato de parecer",
    ])


def classify_type(text_norm: str, title_norm: str):
    # Parecer/Súmula não são classificados como ato operativo de curso.
    if title_is_non_operational(title_norm):
        return "OUTRO", []

    hits = []

    for label, patterns in TYPE_PATTERNS:
        if any(re.search(p, text_norm, re.I) for p in patterns):
            hits.append(label)

    order = [
        "RENOVAÇÃO DE RECONHECIMENTO DE CURSO",
        "RECONHECIMENTO DE CURSO",
        "AUTORIZAÇÃO DE CURSO",
        "ADITAMENTO DE CURSO",
    ]

    for label in order:
        if label in hits:
            return label, hits

    return "OUTRO", hits


def classify_object(text_norm: str, title_norm: str, act_type: str):
    reasons = []

    if "estatuto" in title_norm or any(x in title_norm for x in INSTITUTIONAL_TERMS):
        return "INSTITUCIONAL/ESTATUTO", reasons

    if title_is_non_operational(title_norm):
        return "PARECER/SÚMULA", reasons

    institutional_hits = [x for x in INSTITUTIONAL_TERMS if x in text_norm]
    campus_hits = [x for x in CAMPUS_TERMS if x in text_norm]
    procedural_hits = [x for x in PROCEDURAL_TERMS if x in text_norm]

    strong_hits = [x for x in COURSE_STRONG if x in text_norm]
    context_hits = [x for x in COURSE_CONTEXT if x in text_norm]
    degree_hits = [x for x in COURSE_DEGREE if x in text_norm]

    # Um ato explicitamente institucional não deve virar ato de curso
    # apenas porque menciona algum curso no corpo do documento.
    if institutional_hits and act_type == "OUTRO":
        return "INSTITUCIONAL/IES", reasons

    # Campus/unidade só vira curso quando existe evidência regulatória
    # operacional ligada a curso.
    course_regulatory_evidence = (
        len(strong_hits) >= 1
        and (
            "e-mec" in text_norm
            or "emec" in text_norm
            or "vagas totais anuais" in text_norm
            or ("carga horária total" in text_norm and degree_hits)
            or ("mantida pela" in text_norm and degree_hits)
            or ("mantida por" in text_norm and degree_hits)
        )
    )

    if campus_hits and not course_regulatory_evidence:
        return "CAMPUS/UNIDADE", reasons

    # Curso: exigimos ato regulatório + linguagem explícita de curso
    # + pelo menos uma evidência regulatória/operacional.
    if act_type in REGULATORY_TYPES:
        if strong_hits and (
            "e-mec" in text_norm
            or "emec" in text_norm
            or "vagas totais anuais" in text_norm
            or ("carga horária total" in text_norm and degree_hits)
            or ("mantida pela" in text_norm and degree_hits)
            or ("mantida por" in text_norm and degree_hits)
        ):
            return "CURSO", reasons

    if institutional_hits:
        return "INSTITUCIONAL/IES", reasons

    if campus_hits:
        return "CAMPUS/UNIDADE", reasons

    if procedural_hits:
        return "PROCEDIMENTAL", reasons

    return "OUTRO", reasons


def detect_effect(text_norm):
    for label, patterns in EFFECT_PATTERNS:
        if any(re.search(p, text_norm) for p in patterns):
            return label
    return ""


def extract_fields(text):
    out = {
        "ies": "",
        "mantenedora": "",
        "curso": "",
        "grau": "",
        "modalidade": "",
        "local": "",
        "vagas": "",
        "codigo_emec": "",
    }

    degree_patterns = [
        ("Bacharelado", r"\bbacharelado\b"),
        ("Licenciatura", r"\blicenciatura\b"),
        ("Tecnológico", r"\btecnolog(?:ia|ico)\b"),
    ]

    for label, pat in degree_patterns:
        if re.search(pat, norm(text), re.I):
            out["grau"] = label
            break

    if re.search(r"\bEAD\b|educação a distância|educacao a distancia", text, re.I):
        out["modalidade"] = "EaD"
    elif re.search(r"\bpresencial\b", text, re.I):
        out["modalidade"] = "Presencial"

    m = re.search(r"\be-?mec\s*(?:n[º°]\s*)?([0-9]{4,12})", text, re.I)
    if m:
        out["codigo_emec"] = m.group(1)

    m = re.search(r"vagas(?: totais anuais)?\s*[:\-]?\s*([0-9]{1,5})", text, re.I)
    if m:
        out["vagas"] = m.group(1)

    patterns = [
        r"curso(?: superior)?(?: de graduação)?\s*[:\-]\s*([^;|]{5,140})",
        r"curso\s+de\s+([^;|]{5,140})",
        r"(?:bacharelado|licenciatura|tecnologia)\s+em\s+([^;|]{4,120})",
    ]

    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            val = clean_text(m.group(1))
            if 4 <= len(val) <= 140:
                out["curso"] = val
                break

    return out


def individual_dou_url(meta):
    for key in ("url", "urltitle"):
        val = meta.get(key, "")
        if val:
            if val.startswith("http"):
                return val
            if key == "urltitle":
                return "https://www.in.gov.br/web/dou/-/" + val.lstrip("/")
    return ""


def confidence_score(act_type, obj, title_norm, text_norm, fields):
    score = 0
    reasons = []

    if act_type in REGULATORY_TYPES:
        score += 35
        reasons.append("tipo regulatório de curso identificado")

    if obj == "CURSO":
        score += 35
        reasons.append("objeto explicitamente classificado como curso")

    if fields["codigo_emec"]:
        score += 10
        reasons.append("código e-MEC identificado")

    if fields["curso"] and fields["grau"]:
        score += 10
        reasons.append("curso e grau identificados")

    if any(x in text_norm for x in [
        "vagas totais anuais", "carga horária total",
        "mantida pela", "mantida por", "ofertado pela",
        "ofertada pela",
    ]):
        score += 10
        reasons.append("contexto regulatório/oferta identificado")

    if title_is_non_operational(title_norm):
        score = min(score, 20)
        reasons.append("documento não operacional (parecer/súmula/despacho)")

    return min(score, 100), reasons


def validation_status(act_type, obj, score, title_norm, text_norm):
    if act_type in REGULATORY_TYPES and obj == "CURSO" and score >= 70:
        return "CANDIDATO_ATO_DE_CURSO"

    # Casos que parecem regulatórios, mas não têm evidência suficiente,
    # ficam disponíveis para revisão humana.
    possible = (
        any(x in text_norm for x in [
            "seres", "secretaria de regulacao e supervisao da educacao superior",
            "secretaria de regulação e supervisão da educação superior",
        ])
        and any(x in text_norm for x in [
            "autoriza", "reconhece", "reconhecimento",
            "renova", "aditamento",
        ])
        and any(x in text_norm for x in ["curso", "bacharelado", "licenciatura", "tecnologia"])
    )

    if possible or (act_type in REGULATORY_TYPES and score >= 40):
        return "FILA_VALIDACAO"

    return "FORA_DO_ESCOPO_DE_ATO_DE_CURSO"


def build_record(meta, parts, full_text, date_obj, section, xml_name):
    title = meta.get("name") or parts.get("Titulo") or ""

    body = " ".join([
        parts.get("Identifica", ""),
        parts.get("Ementa", ""),
        parts.get("Titulo", ""),
        parts.get("SubTitulo", ""),
        parts.get("Texto", ""),
    ])

    text_n = norm(body)
    title_n = norm(title)

    act_type, type_hits = classify_type(text_n, title_n)
    obj, _ = classify_object(text_n, title_n, act_type)
    effect = detect_effect(text_n)
    number, year = parse_act_number_year(meta, parts)
    fields = extract_fields(body)

    score, score_reasons = confidence_score(
        act_type, obj, title_n, text_n, fields
    )

    status = validation_status(
        act_type, obj, score, title_n, text_n
    )

    page_url = ""
    if meta.get("idMateria"):
        page_url = (
            "https://pesquisa.in.gov.br/imprensa/jsp/visualiza/index.jsp"
            f"?data={date_obj.strftime('%d/%m/%Y')}&jornal=515"
            f"&pagina={meta.get('numberPage','')}"
        )

    individual = individual_dou_url(meta)

    return {
        "id": meta.get("idMateria") or meta.get("id") or xml_name,
        "data_coleta": datetime.now(timezone.utc).isoformat(),
        "versao_classificacao": "V10.8",
        "edicao": {
            "data": date_obj.isoformat(),
            "secao": section,
            "numero": meta.get("editionNumber", ""),
            "pagina": meta.get("numberPage", ""),
        },
        "identificacao_dou": {
            "idMateria": meta.get("idMateria", ""),
            "idOficio": meta.get("idOficio", ""),
            "article_id": meta.get("id", ""),
            "artType": meta.get("artType", ""),
            "pubName": meta.get("pubName", ""),
        },
        "titulo": title,
        "numero_ato": number,
        "ano": year,
        "tipo_ato_classificado": act_type,
        "efeito_detectado": effect,
        "objeto_classificado": obj,
        "confianca": {
            "score": score,
            "motivos": score_reasons,
        },
        "evidencias": {
            "tipos_detectados": type_hits,
            "termos_curso": [x for x in COURSE_STRONG if x in text_n],
            "contexto_curso": [x for x in COURSE_CONTEXT if x in text_n],
            "termos_institucionais": [x for x in INSTITUTIONAL_TERMS if x in text_n],
            "termos_campus_unidade": [x for x in CAMPUS_TERMS if x in text_n],
        },
        "campos_extraidos": fields,
        "fonte": {
            "pagina_edicao": page_url,
            "publicacao_individual": individual,
            "individual_confirmada": bool(individual),
        },
        "status_v10_8": status,
        "confirmado": False,
        "validacao_pendente": status in {
            "CANDIDATO_ATO_DE_CURSO",
            "FILA_VALIDACAO",
        },
        "trecho_para_validacao": clean_text(body)[:5000],
    }


def main():
    MONITORING.mkdir(exist_ok=True)
    session = requests.Session()

    dates = parse_date_list()
    downloads = []
    records = []
    errors = []

    try:
        login(session)
    except Exception as exc:
        payload = {
            "version": "V10.8",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "status": "erro_login",
            "erro": str(exc),
        }
        (MONITORING / "atos_validacao_v10_8.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        sys.exit(1)

    seen = set()

    for date_obj in dates:
        for section in SECTIONS:
            try:
                result = download_zip(session, date_obj, section)

                downloads.append({
                    k: v for k, v in result.items() if k != "content"
                })

                if not result["is_zip"]:
                    errors.append({
                        "date": result["date"],
                        "section": section,
                        "error": "Resposta não é ZIP.",
                        "final_url": result["final_url"],
                        "http_status": result["http_status"],
                    })
                    continue

                with zipfile.ZipFile(io.BytesIO(result["content"])) as zf:
                    for member in zf.namelist():
                        if not member.lower().endswith(".xml"):
                            continue

                        try:
                            meta, parts, full_text = extract_article(zf.read(member))

                            rec = build_record(
                                meta, parts, full_text,
                                date_obj, section, member
                            )

                            key = (
                                rec["identificacao_dou"]["idMateria"]
                                or rec["identificacao_dou"]["article_id"]
                                or member
                            )

                            if key in seen:
                                continue

                            seen.add(key)
                            records.append(rec)

                        except Exception as exc:
                            errors.append({
                                "date": result["date"],
                                "section": section,
                                "xml": member,
                                "error": f"parse: {exc}",
                            })

            except Exception as exc:
                errors.append({
                    "date": date_obj.isoformat(),
                    "section": section,
                    "error": str(exc),
                })

    confirmed_candidates = [
        r for r in records
        if r["status_v10_8"] == "CANDIDATO_ATO_DE_CURSO"
    ]

    validation_queue = [
        r for r in records
        if r["status_v10_8"] == "FILA_VALIDACAO"
    ]

    out_scope = [
        r for r in records
        if r["status_v10_8"] == "FORA_DO_ESCOPO_DE_ATO_DE_CURSO"
    ]

    payload = {
        "version": "V10.8",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "objetivo": (
            "Captura universal e classificação conservadora de atos regulatórios "
            "de cursos, com fila de validação e score de confiança."
        ),
        "escopo_ato_de_curso": sorted(REGULATORY_TYPES),
        "downloads": downloads,
        "resumo": {
            "xmls_processados": len(records),
            "candidatos_ato_de_curso": len(confirmed_candidates),
            "fila_validacao": len(validation_queue),
            "fora_do_escopo": len(out_scope),
            "erros": len(errors),
        },
        "candidatos": confirmed_candidates,
        "fila_validacao": validation_queue,
        "fora_do_escopo_amostragem": out_scope,
        "erros": errors,
    }

    (MONITORING / "atos_validacao_v10_8.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md = [
        "# Oráculo — V10.8 | Validação de Atos de Curso",
        "",
        f"- Execução: `{payload['executed_at']}`",
        f"- XMLs processados: **{len(records)}**",
        f"- Candidatos de ato de curso: **{len(confirmed_candidates)}**",
        f"- Fila de validação: **{len(validation_queue)}**",
        f"- Fora do escopo: **{len(out_scope)}**",
        f"- Erros: **{len(errors)}**",
        "",
        "## Candidatos de ato de curso",
        "",
    ]

    if not confirmed_candidates:
        md.append("Nenhum candidato de ato de curso foi classificado.")
    else:
        for i, r in enumerate(
            sorted(
                confirmed_candidates,
                key=lambda x: x["confianca"]["score"],
                reverse=True,
            ),
            1,
        ):
            f = r["campos_extraidos"]

            md.extend([
                f"### {i}. {r['titulo']}",
                f"- Tipo: **{r['tipo_ato_classificado']}**",
                f"- Objeto: **{r['objeto_classificado']}**",
                f"- Confiança: **{r['confianca']['score']}/100**",
                f"- Efeito: `{r['efeito_detectado']}`",
                f"- Número/ano: `{r['numero_ato']}/{r['ano']}`",
                f"- IES: `{f['ies']}`",
                f"- Curso: `{f['curso']}`",
                f"- Grau: `{f['grau']}`",
                f"- Modalidade: `{f['modalidade']}`",
                f"- e-MEC: `{f['codigo_emec']}`",
                f"- Fonte individual: `{r['fonte']['publicacao_individual']}`",
                "",
            ])

    md.extend([
        "## Fila de validação",
        "",
    ])

    if not validation_queue:
        md.append("Nenhum item enviado para validação humana.")
    else:
        for i, r in enumerate(
            sorted(
                validation_queue,
                key=lambda x: x["confianca"]["score"],
                reverse=True,
            ),
            1,
        ):
            md.extend([
                f"### {i}. {r['titulo']}",
                f"- Tipo: **{r['tipo_ato_classificado']}**",
                f"- Objeto: **{r['objeto_classificado']}**",
                f"- Confiança: **{r['confianca']['score']}/100**",
                f"- Número/ano: `{r['numero_ato']}/{r['ano']}`",
                "",
            ])

    (MONITORING / "atos_validacao_v10_8.md").write_text(
        "\n".join(md),
        encoding="utf-8",
    )

    print("===== RESUMO V10.8 =====")
    print(f"XMLs processados: {len(records)}")
    print(f"Candidatos de ato de curso: {len(confirmed_candidates)}")
    print(f"Fila de validação: {len(validation_queue)}")
    print(f"Fora do escopo: {len(out_scope)}")
    print(f"Erros: {len(errors)}")

    print("\n===== CANDIDATOS =====")
    for i, r in enumerate(confirmed_candidates, 1):
        print(
            f"{i}. [{r['tipo_ato_classificado']}] "
            f"{r['titulo']} | confiança={r['confianca']['score']}"
        )
        print(
            f"   Objeto: {r['objeto_classificado']} | "
            f"Número/ano: {r['numero_ato']}/{r['ano']}"
        )

    print("\n===== FILA DE VALIDAÇÃO =====")
    for i, r in enumerate(validation_queue, 1):
        print(
            f"{i}. [{r['tipo_ato_classificado']}] "
            f"{r['titulo']} | confiança={r['confianca']['score']}"
        )


if __name__ == "__main__":
    main()
