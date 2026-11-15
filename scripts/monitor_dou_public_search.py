#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fallback do buscador oficial do DOU: assuntos + autoridades educacionais.
Não confirma atos e não altera o Radar.
"""
from __future__ import annotations
import json, os, re, sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

BASE_URL="https://www.in.gov.br/consulta/-/buscar/dou"
ARTICLE_BASE="https://www.in.gov.br/web/dou/-/"
OUTPUT=Path(__file__).resolve().parents[1]/"monitoring"/"dou_public_search_fallback.json"
TERMS=(
 "autorização de curso","reconhecimento de curso",
 "renovação de reconhecimento de curso","aditamento de curso",
 '"Secretaria de Regulação e Supervisão da Educação Superior"',
 '"Portaria SERES/MEC"',
 '"Conselho Nacional de Educação" educação superior',
 '"Instituto Nacional de Estudos e Pesquisas Educacionais" educação superior',
)
AUTHORITY_MARKERS=("ministério da educação","ministerio da educacao","seres",
 "secretaria de regulação e supervisão da educação superior",
 "secretaria de regulacao e supervisao da educacao superior",
 "conselho nacional de educação","conselho nacional de educacao",
 "instituto nacional de estudos e pesquisas educacionais","inep")
DAYS_BACK=max(0,int(os.getenv("DOU_FALLBACK_DAYS_BACK","6")))
TIMEOUT=int(os.getenv("DOU_FALLBACK_TIMEOUT","25"))
DETAIL_LIMIT=max(0,int(os.getenv("DOU_DETAIL_LIMIT","40")))
UA="Mozilla/5.0 (compatible; OraculoSecretariaAcademica/1.0)"
RETRY_TOTAL=max(0,int(os.getenv("DOU_HTTP_RETRIES","3")))
RETRY_BACKOFF=float(os.getenv("DOU_HTTP_BACKOFF","1.5"))
RETRY_STATUSES=(429,500,502,503,504)

def build_session()->requests.Session:
 session=requests.Session()
 retry=Retry(total=RETRY_TOTAL,connect=RETRY_TOTAL,read=RETRY_TOTAL,
  status=RETRY_TOTAL,backoff_factor=RETRY_BACKOFF,status_forcelist=RETRY_STATUSES,
  allowed_methods=frozenset({"GET"}),raise_on_status=False)
 adapter=HTTPAdapter(max_retries=retry)
 session.mount("https://",adapter);session.mount("http://",adapter)
 return session

def parse_results(html:bytes)->list[dict]:
 soup=BeautifulSoup(html,"html.parser")
 tag=soup.find("script",id="_br_com_seatecnologia_in_buscadou_BuscaDouPortlet_params")
 if tag is None or (not tag.string and not tag.contents): raise RuntimeError("Estrutura de resultados do buscador DOU não localizada.")
 payload=json.loads(tag.string or tag.contents[0]); results=[]
 for item in payload.get("jsonArray") or []:
  slug=str(item.get("urlTitle") or "").strip()
  results.append({"id":str(item.get("classPK") or ""),"title":str(item.get("title") or ""),
   "abstract":str(item.get("content") or ""),"date":str(item.get("pubDate") or ""),
   "section":str(item.get("pubName") or ""),"act_type":str(item.get("artType") or ""),
   "url":ARTICLE_BASE+slug if slug else "","hierarchy":str(item.get("hierarchyStr") or "")})
 return results

def search(term:str,start:datetime,end:datetime,section:str="do1",session:requests.Session|None=None)->list[dict]:
 params={"q":term,"exactDate":"personalizado","publishFrom":start.strftime("%d-%m-%Y"),
  "publishTo":end.strftime("%d-%m-%Y"),"sortType":"0","s":section}
 client=session or build_session()\n r=client.get(BASE_URL,params=params,headers={"User-Agent":UA,"Accept":"text/html,application/xhtml+xml;q=0.9,*/*;q=0.8","Cache-Control":"no-cache"},timeout=TIMEOUT)
 r.raise_for_status()
 items=parse_results(r.content)
 for x in items:x["query_term"]=term
 return items

def is_education_authority(item:dict)->bool:
 text=" ".join(str(item.get(k) or "") for k in ("title","hierarchy","abstract")).lower()
 return any(x in text for x in AUTHORITY_MARKERS)

def extract_article_text(html:bytes)->str:
 soup=BeautifulSoup(html,"html.parser")
 for n in soup(["script","style","noscript","nav","footer","header"]):n.decompose()
 candidates=[]
 for selector in ("#materia",".materia-dou",".texto-dou",".detalhamento-materia",".conteudo-materia","article"):
  for node in soup.select(selector):
   value=re.sub(r"\s+"," ",node.get_text(" ",strip=True)).strip()
   if len(value)>250:candidates.append(value)
 if candidates:return max(candidates,key=len)[:50000]
 body=soup.body or soup
 value=re.sub(r"\s+"," ",body.get_text(" ",strip=True)).strip()
 return value[:50000] if len(value)>500 else ""

def fetch_article(item:dict,session:requests.Session)->dict:
 url=str(item.get("url") or "")
 if not url.startswith("https://www.in.gov.br/"):return {"full_text_status":"url_invalida"}
 try:
  r=session.get(url,timeout=TIMEOUT,headers={"User-Agent":UA});r.raise_for_status()
  text=extract_article_text(r.content)
  if not text:return {"full_text_status":"texto_nao_localizado","article_http_status":r.status_code}
  return {"full_text":text,"full_text_status":"obtido","article_http_status":r.status_code,
   "full_text_fetched_at":datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat()}
 except requests.RequestException as exc:
  return {"full_text_status":"erro_consulta","full_text_error":f"{type(exc).__name__}: {exc}"}

def collect()->dict:
 end=datetime.now(ZoneInfo("America/Sao_Paulo")).replace(hour=23,minute=59,second=59,microsecond=0)
 start=(end-timedelta(days=DAYS_BACK)).replace(hour=0,minute=0,second=0,microsecond=0)
 found={};errors=[];queries=[]
 for term in TERMS:
  try:
   items=search(term,start,end);queries.append({"term":term,"results":len(items),"status":"ok"})
   for item in items:
    key=item["id"] or item["url"] or (item["title"]+item["date"])
    if not key:continue
    if key in found:
     terms=set(found[key].get("query_terms",[found[key].get("query_term","")]));terms.add(term)
     found[key]["query_terms"]=sorted(x for x in terms if x)
    else:item["query_terms"]=[term];found[key]=item
  except Exception as exc:
   queries.append({"term":term,"status":"erro"});errors.append({"term":term,"error":f"{type(exc).__name__}: {exc}"})
 results=sorted(found.values(),key=lambda x:(x["date"],x["title"]),reverse=True)
 authority=[x for x in results if is_education_authority(x)]
 session=requests.Session()
 for item in authority[:DETAIL_LIMIT]:item.update(fetch_article(item,session))
 return {"schema_version":2,"collected_at":datetime.now(ZoneInfo("America/Sao_Paulo")).isoformat(),
  "source":"Buscador oficial do Diário Oficial da União — Imprensa Nacional","endpoint":BASE_URL,
  "period":{"from":start.date().isoformat(),"to":end.date().isoformat()},
  "status":"consulta_concluida" if not errors else ("consulta_parcial" if results else "fonte_indisponivel"),
  "queries":queries,"results_count":len(results),"results":results,
  "education_authority_results":len(authority),"full_text_attempts":min(len(authority),DETAIL_LIMIT),
  "full_text_obtained":sum(x.get("full_text_status")=="obtido" for x in authority),
  "errors":errors,"radar_modified":False,"candidate_acts_created":0,
  "http_retry":{"total":RETRY_TOTAL,"backoff_factor":RETRY_BACKOFF,"retryable_statuses":list(RETRY_STATUSES)},\n  "note":"Busca ampliada por assunto e autoridade emissora com retry HTTP. Inteiros teores são apoio à análise e exigem conferência da publicação oficial. Nenhum ato foi confirmado e o Radar não foi alterado."}

def main()->int:
 report=collect();OUTPUT.parent.mkdir(parents=True,exist_ok=True)
 OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(json.dumps({k:report[k] for k in ("status","results_count","education_authority_results","full_text_attempts","full_text_obtained","errors","radar_modified")},ensure_ascii=False))
 return 0 if report["status"] in {"consulta_concluida","consulta_parcial"} else 1
if __name__=="__main__":sys.exit(main())
