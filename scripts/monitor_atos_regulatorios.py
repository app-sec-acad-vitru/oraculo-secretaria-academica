#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Oráculo da Secretaria Acadêmica — V10.7
Classificação hierárquica de atos regulatórios encontrados no DOU via INLABS.

Objetivo:
1) Autenticar no INLABS.
2) Baixar DO1/DO1E das duas últimas datas configuradas.
3) Ler XMLs.
4) Fazer triagem semântica em duas dimensões:
   - tipo do ato
   - objeto regulatório
5) Considerar como candidato de curso somente quando:
   tipo = ato regulatório de curso
   E
   objeto = CURSO
6) Não alterar atos_regulatorios.json nesta etapa.
7) Gerar:
   monitoring/atos_validacao_v10_7.json
   monitoring/atos_validacao_v10_7.md

A etapa de confirmação definitiva permanece separada.
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

# Termos fortes para caracterização de atos de curso.
COURSE_STRONG = [
    "curso superior de graduação",
    "curso de graduação",
    "curso superior",
    "curso de bacharelado",
    "curso de licenciatura",
    "curso superior de tecnologia",
    "tecnologia em",
    "bacharelado em",
    "licenciatura em",
    "vagas totais anuais",
    "carga horária total",
    "e-mec",
    "emec",
    "mantida",
    "mantenedora",
    "grau",
]

COURSE_DEGREE = [
    "bacharelado", "licenciatura", "tecnologia", "tecnólogo",
]

COURSE_CONTEXT = [
    "ofertado pela", "ofertada pela", "a ser ofertado",
    "a ser ofertada", "mantida por", "mantida pela",
    "mantenedora", "vagas", "turno", "carga horária",
    "endereço", "município", "campus", "polo",
]

# Objetos que NÃO devem entrar como atos de curso.
OBJECT_EXCLUSIONS = [
    "funcionamento de novas unidades de ensino",
    "nova unidade de ensino",
    "funcionamento de campus",
    "novo campus",
    "campus avançado",
    "credenciamento da instituição",
    "recredenciamento da instituição",
    "credenciamento institucional",
    "recredenciamento institucional",
    "instituição comunitária de educação superior",
    "instituição de educação superior",
    "processo seletivo",
    "concurso público",
    "tomada de contas",
    "pauta de julgamento",
]

NON_COURSE_CONTEXT = [
    "concurso público", "processo seletivo", "conselho regional",
    "conselho federal", "tomada de contas", "licitação",
    "campus", "unidade de ensino",
]

TYPE_PATTERNS = [
    ("RENOVAÇÃO DE RECONHECIMENTO DE CURSO", [
        r"renova(?:ção|cao)\s+do\s+reconhecimento",
        r"renova(?:ção|cao)\s+de\s+reconhecimento",
    ]),
    ("RECONHECIMENTO DE CURSO", [
        r"reconhece\s+o\s+curso",
        r"reconhecimento\s+do\s+curso",
        r"reconhecimento\s+de\s+curso",
    ]),
    ("AUTORIZAÇÃO DE CURSO", [
        r"autoriza\s+(?:o|a)\s+(?:funcionamento|oferta|curso)",
        r"autoriza(?:ção|cao)\s+(?:de|do|da)\s+curso",
        r"autoriza(?:ção|cao)\s+.*curso",
    ]),
    ("ADITAMENTO DE CURSO", [
        r"aditamento.*curso",
        r"altera(?:ção|cao).*curso",
        r"alter(?:a|ação|acao).*curso",
    ]),
]

EFFECT_PATTERNS = [
    ("renova", [r"renova(?:ção|cao)"]),
    ("reconhece", [r"reconhecimento", r"\breconhece\b"]),
    ("autoriza", [r"autoriza(?:ção|cao)", r"\bautoriza\b"]),
    ("adita", [r"\baditamento\b", r"\badita\b"]),
    ("indefere", [r"\bindefere\b", r"indeferimento"]),
    ("anula", [r"\banula\b", r"anulação", r"anulacao"]),
    ("torna sem efeito", [r"torna\s+sem\s+efeito"]),
    ("restabelece", [r"restabelece", r"restabelecimento"]),
]


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = html_lib.unescape(s)
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
    text = r.text or ""
    n = norm(text)

    if "este endereço de email não existe" in n or "senha incorreta" in n:
        raise RuntimeError("Credenciais INLABS rejeitadas.")
    if "sair" not in n or "minha conta" not in n:
        raise RuntimeError(
            f"Login INLABS não confirmado. final_url={r.url} status={r.status_code}"
        )

    return r


def download_zip(session: requests.Session, date_obj, section: str):
    # Mecânica compatível com o fluxo INLABS: YYYY-MM-DD/DO1.zip
    date_str = date_obj.strftime("%Y-%m-%d")
    url = f"{DOWNLOAD_BASE}&dl={date_str}-{section}.zip"
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


def find_text(root, tags):
    wanted = {t.lower() for t in tags}
    for el in root.iter():
        tag = el.tag.split("}")[-1].lower()
        if tag in wanted:
            txt = clean_text(" ".join(el.itertext()))
            if txt:
                return txt
    return ""


def extract_article(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    attrs = dict(root.attrib)

    # Em alguns lotes o article está em nível abaixo de <xml>.
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

    # Texto completo limitado para diagnóstico/revisão.
    full_text = xml_to_text(article)

    return meta, parts, full_text


def parse_act_number_year(meta, parts):
    source = " ".join([
        meta.get("name", ""),
        parts.get("Identifica", ""),
        parts.get("Titulo", ""),
        parts.get("SubTitulo", ""),
    ])
    m = re.search(
        r"(?:N[º°o]\s*)?([0-9]{1,5}(?:[-/][A-Z0-9]+)?)\s*,?\s*DE\s+"
        r"\d{1,2}\s+DE\s+[A-ZÇÃÕÉÊÍÓÚ]+\s+DE\s+(\d{4})",
        source,
        re.I,
    )
    if not m:
        m = re.search(r"N[º°o]\s*([0-9]{1,6}).{0,120}?(\d{4})", source, re.I)
    return (m.group(1), int(m.group(2))) if m else ("", None)


def classify_type(text_norm):
    hits = []
    for label, patterns in TYPE_PATTERNS:
        if any(re.search(p, text_norm, re.I) for p in patterns):
            hits.append(label)

    # Prefer the most specific type.
    order = [
        "RENOVAÇÃO DE RECONHECIMENTO DE CURSO",
        "RECONHECIMENTO DE CURSO",
        "AUTORIZAÇÃO DE CURSO",
        "ADITAMENTO DE CURSO",
    ]
    for x in order:
        if x in hits:
            return x, hits
    return "OUTRO", hits


def classify_object(text_norm, act_type):
    exclusion_hits = [x for x in OBJECT_EXCLUSIONS if x in text_norm]
    campus_hits = [
        x for x in ["campus", "unidade de ensino", "funcionamento de novas unidades"]
        if x in text_norm
    ]

    strong_course = [x for x in COURSE_STRONG if x in text_norm]
    context_course = [x for x in COURSE_CONTEXT if x in text_norm]
    degree_hits = [x for x in COURSE_DEGREE if x in text_norm]

    # Se o próprio ato fala explicitamente em campus/unidade e não há
    # evidência suficiente de curso, classificar como objeto institucional.
    if any(x in text_norm for x in [
        "funcionamento de novas unidades de ensino",
        "nova unidade de ensino",
        "funcionamento de campus",
    ]) and len(strong_course) < 3:
        return "CAMPUS/UNIDADE", strong_course, context_course, campus_hits

    if act_type != "OUTRO" and (len(strong_course) >= 2 or len(degree_hits) >= 1) and (
        len(context_course) >= 2 or "e-mec" in text_norm or "emec" in text_norm
    ):
        return "CURSO", strong_course, context_course, campus_hits

    if "mantenedora" in text_norm and len(strong_course) < 2:
        return "MANTENEDORA/IES", strong_course, context_course, campus_hits

    if campus_hits:
        return "CAMPUS/UNIDADE", strong_course, context_course, campus_hits

    return "OUTRO", strong_course, context_course, campus_hits


def detect_effect(text_norm):
    for label, patterns in EFFECT_PATTERNS:
        if any(re.search(p, text_norm) for p in patterns):
            return label
    return ""


def extract_fields(text):
    # Heurísticas conservadoras. Campos não encontrados permanecem vazios.
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

    # Curso: tenta padrões comuns de tabelas/ementas.
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
    # Só usa URL individual se o XML fornecer um identificador explícito.
    for key in ("url", "urltitle"):
        val = meta.get(key, "")
        if val:
            if val.startswith("http"):
                return val
            if key == "urltitle":
                return "https://www.in.gov.br/web/dou/-/" + val.lstrip("/")
    return ""


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

    act_type, type_hits = classify_type(text_n)
    obj, strong, context, campus_hits = classify_object(text_n, act_type)
    effect = detect_effect(text_n)
    number, year = parse_act_number_year(meta, parts)
    fields = extract_fields(body)

    page_url = ""
    if meta.get("idMateria"):
        page_url = (
            "https://pesquisa.in.gov.br/imprensa/jsp/visualiza/index.jsp"
            f"?data={date_obj.strftime('%d/%m/%Y')}&jornal=515"
            f"&pagina={meta.get('numberPage','')}"
        )

    individual = individual_dou_url(meta)

    eligible = (
        act_type in {
            "AUTORIZAÇÃO DE CURSO",
            "RECONHECIMENTO DE CURSO",
            "RENOVAÇÃO DE RECONHECIMENTO DE CURSO",
            "ADITAMENTO DE CURSO",
        }
        and obj == "CURSO"
    )

    # Mantém um trecho para validação humana, sem gravar o XML inteiro.
    snippet = clean_text(body)[:5000]

    return {
        "id": meta.get("idMateria") or meta.get("id") or xml_name,
        "data_coleta": datetime.now(timezone.utc).isoformat(),
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
        "evidencias": {
            "tipos_detectados": type_hits,
            "termos_curso": strong,
            "contexto_curso": context,
            "termos_campus_unidade": campus_hits,
        },
        "campos_extraidos": fields,
        "fonte": {
            "pagina_edicao": page_url,
            "publicacao_individual": individual,
            "individual_confirmada": bool(individual),
        },
        "status_v10_7": (
            "CANDIDATO_ATO_DE_CURSO"
            if eligible
            else "FORA_DO_ESCOPO_DE_ATO_DE_CURSO"
        ),
        "confirmado": False,
        "validacao_pendente": bool(eligible),
        "trecho_para_validacao": snippet,
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
            "version": "V10.7",
            "executed_at": datetime.now(timezone.utc).isoformat(),
            "status": "erro_login",
            "erro": str(exc),
        }
        (MONITORING / "atos_validacao_v10_7.json").write_text(
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
                downloads.append({k: v for k, v in result.items() if k != "content"})
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
                                meta, parts, full_text, date_obj, section, member
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

    course_candidates = [
        r for r in records if r["status_v10_7"] == "CANDIDATO_ATO_DE_CURSO"
    ]

    # Não toca no atos_regulatorios.json.
    payload = {
        "version": "V10.7",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "objetivo": "Classificar tipo x objeto e separar atos de curso de atos institucionais/campus.",
        "escopo_ato_de_curso": [
            "AUTORIZAÇÃO DE CURSO",
            "RECONHECIMENTO DE CURSO",
            "RENOVAÇÃO DE RECONHECIMENTO DE CURSO",
            "ADITAMENTO DE CURSO",
        ],
        "downloads": downloads,
        "resumo": {
            "xmls_processados": len(records),
            "candidatos_ato_de_curso": len(course_candidates),
            "fora_do_escopo": len(records) - len(course_candidates),
            "erros": len(errors),
        },
        "candidatos": course_candidates,
        "fora_do_escopo_amostragem": [
            r for r in records if r["status_v10_7"] != "CANDIDATO_ATO_DE_CURSO"
        ],
        "erros": errors,
    }

    (MONITORING / "atos_validacao_v10_7.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md = [
        "# Oráculo — V10.7 | Validação de Atos de Curso",
        "",
        f"- Execução: `{payload['executed_at']}`",
        f"- XMLs processados: **{len(records)}**",
        f"- Candidatos de ato de curso: **{len(course_candidates)}**",
        f"- Fora do escopo: **{len(records) - len(course_candidates)}**",
        f"- Erros: **{len(errors)}**",
        "",
        "## Candidatos de ato de curso",
        "",
    ]

    if not course_candidates:
        md.append("Nenhum candidato de ato de curso foi classificado nesta execução.")
    else:
        for i, r in enumerate(course_candidates, 1):
            f = r["campos_extraidos"]
            md.extend([
                f"### {i}. {r['titulo']}",
                f"- Tipo: **{r['tipo_ato_classificado']}**",
                f"- Objeto: **{r['objeto_classificado']}**",
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

    (MONITORING / "atos_validacao_v10_7.md").write_text(
        "\n".join(md), encoding="utf-8"
    )

    print(f"XMLs processados: {len(records)}")
    print(f"Candidatos de ato de curso: {len(course_candidates)}")
    print(f"Fora do escopo: {len(records) - len(course_candidates)}")
    print(f"Erros: {len(errors)}")
    for i, r in enumerate(course_candidates, 1):
        print(f"{i}. [{r['tipo_ato_classificado']}] {r['titulo']}")
        print(f"   Objeto: {r['objeto_classificado']}")
        print(f"   Número/ano: {r['numero_ato']}/{r['ano']}")
        print(f"   Fonte: {r['fonte']['pagina_edicao']}")


if __name__ == "__main__":
    main()
