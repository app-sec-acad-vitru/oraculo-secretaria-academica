#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Triagem conservadora do DOU por autoridade, assunto e texto integral.
Nunca confirma atos nem altera o Radar.
"""
from __future__ import annotations
import json,re,sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
INPUT=ROOT/"monitoring"/"dou_public_search_fallback.json"
OUTPUT=ROOT/"monitoring"/"dou_public_search_triage.json"
AUTHORITIES=("ministério da educação","ministerio da educacao","seres",
 "secretaria de regulação e supervisão da educação superior","secretaria de regulacao e supervisao da educacao superior",
 "conselho nacional de educação","conselho nacional de educacao","câmara de educação superior","camara de educacao superior",
 "instituto nacional de estudos e pesquisas educacionais","inep")
ACADEMIC=("educação superior","educacao superior","instituição de educação superior","instituicao de educacao superior",
 "ies","graduação","graduacao","curso superior","curso de graduação","curso de graduacao","reconhecimento de curso",
 "autorização de curso","autorizacao de curso","renovação de reconhecimento","renovacao de reconhecimento",
 "diploma digital","censo da educação superior","censo da educacao superior","enade","diretrizes curriculares",
 "credenciamento de instituição","credenciamento de instituicao","recredenciamento","acervo acadêmico","acervo academico",
 "registro acadêmico","registro academico")
REGULATORY=("autorização","autorizacao","reconhecimento","renovação","renovacao","credenciamento","recredenciamento",
 "descredenciamento","diretrizes curriculares","diploma","enade","censo da educação superior","censo da educacao superior",
 "acervo acadêmico","acervo academico","supervisão","supervisao","medida cautelar","desativação","desativacao")
IMPACTS={
 "descredenciamento":("descredenciamento","descredenciar"),
 "supervisao_sancao":("medida cautelar","penalidade","sanção","sancao","processo administrativo sancionador"),
 "credenciamento_ies":("credenciamento","recredenciamento"),
 "regulacao_curso":("autorização de curso","autorizacao de curso","reconhecimento de curso","renovação de reconhecimento","renovacao de reconhecimento"),
 "acervo_e_documentacao":("acervo acadêmico","acervo academico","guarda do acervo","registros acadêmicos","registros academicos"),
 "diretrizes_curriculares":("diretrizes curriculares","currículo","curriculo"),
 "avaliacao_inep":("enade","censo da educação superior","censo da educacao superior","avaliação in loco","avaliacao in loco")}

def normalize(value:str)->str:
 return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",str(value or ""))).strip().lower()

def classify(item:dict)->dict:
 hierarchy=normalize(item.get("hierarchy",""));title=normalize(item.get("title",""))
 text=" ".join((title,normalize(item.get("abstract","")),hierarchy,normalize(item.get("full_text",""))))
 authority=[x for x in AUTHORITIES if x in hierarchy or x in title]
 academic=[x for x in ACADEMIC if x in text];regulatory=[x for x in REGULATORY if x in text]
 if authority and (academic or regulatory):status,reason="candidato_relevante","Autoridade educacional identificada e assunto regulatório compatível."
 elif authority:status,reason="revisao_manual","Autoridade educacional identificada; assunto precisa de conferência."
 elif academic and regulatory and any(x in text for x in ("mec","seres","cne","inep","educação superior","educacao superior")):
  status,reason="revisao_manual","Assunto acadêmico identificado, mas a autoridade emissora não foi confirmada."
 else:status,reason="fora_do_escopo","Não há evidência suficiente de vínculo com regulação da educação superior."
 impacts=[name for name,terms in IMPACTS.items() if any(term in text for term in terms)]
 return {**item,"triage":{"status":status,"reason":reason,"authority_matches":authority,
  "academic_matches":academic,"regulatory_matches":regulatory,"impact_types":impacts,
  "full_text_available":item.get("full_text_status")=="obtido",
  "requires_human_validation":status!="fora_do_escopo","confirmed":False}}

def build_report(source:dict)->dict:
 items=[classify(x) for x in source.get("results",[])]
 relevant=[x for x in items if x["triage"]["status"]=="candidato_relevante"]
 manual=[x for x in items if x["triage"]["status"]=="revisao_manual"]
 excluded=[x for x in items if x["triage"]["status"]=="fora_do_escopo"]
 impact_counts={}
 for item in relevant+manual:
  for impact in item["triage"]["impact_types"]:impact_counts[impact]=impact_counts.get(impact,0)+1
 return {"schema_version":2,"generated_at":datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(),
  "source_file":"monitoring/dou_public_search_fallback.json","source_status":source.get("status","desconhecido"),
  "period":source.get("period",{}),"results_count":len(items),
  "counts":{"candidato_relevante":len(relevant),"revisao_manual":len(manual),"fora_do_escopo":len(excluded)},
  "impact_counts":impact_counts,
  "full_text":{"authority_results":source.get("education_authority_results",0),
   "attempts":source.get("full_text_attempts",0),"obtained":source.get("full_text_obtained",0)},
  "results":items,"radar_modified":False,"acts_confirmed":0,
  "note":"Triagem automatizada preliminar por assunto e autoridade emissora. Texto integral não substitui conferência da publicação oficial. Candidatos exigem validação humana; nenhum resultado foi integrado automaticamente ao Radar."}

def main()->int:
 if not INPUT.exists():print(f"Arquivo de entrada não encontrado: {INPUT}",file=sys.stderr);return 2
 source=json.loads(INPUT.read_text(encoding="utf-8"));report=build_report(source)
 OUTPUT.parent.mkdir(parents=True,exist_ok=True);OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(json.dumps({"status":"triagem_concluida","counts":report["counts"],"impact_counts":report["impact_counts"],
  "full_text":report["full_text"],"output":str(OUTPUT),"radar_modified":False},ensure_ascii=False))
 return 0
if __name__=="__main__":sys.exit(main())
