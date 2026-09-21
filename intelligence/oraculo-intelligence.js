/*
 * Oráculo da Secretaria Acadêmica — camada de inteligência V3
 * Correção de consistência/classificação — não altera o layout V5.
 */
(() => {
  "use strict";

  const INTEL_URLS = {
    matriz: "intelligence/matriz_perguntas.json",
    motor: "intelligence/motor_resposta.json",
    respostas: "intelligence/respostas_validadas.json"
  };

  const state = {
    matriz: null,
    motor: null,
    respostas: new Map(),
    weights: new Map(),
    ready: false
  };

  const $i = id => document.getElementById(id);

  const normText = value => String(value ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();

  const escI = value => String(value ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));

  const SYNONYMS = {
    "expedicao":"emissao", "expedir":"emitir", "expedido":"emitido",
    "expedidos":"emitidos", "diplomas":"diploma", "registros":"registro",
    "licenciaturas":"licenciatura", "cursos":"curso", "alunos":"aluno",
    "documentos":"documento", "assinaturas":"assinatura", "historicos":"historico",
    "estagios":"estagio", "supervisionar":"supervisao", "supervisiona":"supervisao",
    "supervisionado":"supervisao", "supervisionados":"supervisao", "prazos":"prazo",
    "datas":"data", "etapas":"etapa", "regras":"regra"
  };

  const STOPWORDS = new Set([
    "a","à","ao","aos","as","às","até","com","como","da","das","de","do","dos",
    "e","em","entre","esse","esta","este","eu","há","isso","isto","na","nas",
    "no","nos","o","os","para","pela","pelas","pelo","pelos","por","qual",
    "quais","que","se","sem","sobre","sua","suas","seu","seus","um","uma","umas",
    "uns","é","são","ser","sido","foi","foram","pode","podem","deve","devem",
    "preciso","precisa","precisam","devo","devemos","funciona","funcionam","base","atual",
    "aparece","aparecem","informam","informa","devo","devem"
  ]);

  function tokens(value) {
    return normText(value)
      .split(/\s+/)
      .map(t => SYNONYMS[t] || t)
      .filter(t => t.length > 2 && !STOPWORDS.has(t));
  }

  function buildWeights() {
    const rules = state.motor?.regras || [];
    const qMap = new Map((state.matriz?.perguntas || []).map(q => [q.id, q]));
    const df = new Map();
    const all = [];

    rules.forEach(rule => {
      const candidates = [
        ...(rule.gatilhos || []),
        ...(qMap.has(rule.id) ? [qMap.get(rule.id).pergunta] : [])
      ];
      const set = new Set(candidates.flatMap(tokens));
      all.push(set);
      set.forEach(t => df.set(t, (df.get(t) || 0) + 1));
    });

    const N = Math.max(all.length, 1);
    state.weights = new Map(
      [...df.entries()].map(([t, n]) => [t, 1 + Math.log(N / n)])
    );
  }

  async function loadJSON(url) {
    const r = await fetch(url + "?v=" + Date.now(), {cache:"no-store"});
    if (!r.ok) throw new Error("Falha ao carregar " + url + " (" + r.status + ")");
    return r.json();
  }

  async function loadIntelligence() {
    try {
      const [matriz, motor, respostas] = await Promise.all([
        loadJSON(INTEL_URLS.matriz),
        loadJSON(INTEL_URLS.motor),
        loadJSON(INTEL_URLS.respostas)
      ]);
      state.matriz = matriz;
      state.motor = motor;
      state.respostas.clear();
      (respostas.respostas || []).forEach(r => state.respostas.set(r.pergunta_id, r));
      buildWeights();
      state.ready = true;
      window.OraculoIntelligence = state;
      console.info("[Oráculo] Inteligência V3 carregada:", {
        perguntas:(matriz.perguntas||[]).length,
        regras:(motor.regras||[]).length,
        respostasValidadas:state.respostas.size
      });
      return true;
    } catch (err) {
      console.error("[Oráculo] Falha ao carregar camada de inteligência:", err);
      state.ready = false;
      return false;
    }
  }

  function ruleById(id) {
    const rule = (state.motor?.regras || []).find(r => r.id === id);
    const matrix = (state.matriz?.perguntas || []).find(q => q.id === id);
    return rule ? {rule, matrix, score:1000} : null;
  }

  function exactMatch(query) {
    const q = normText(query);
    if (!q) return null;
    const questions = state.matriz?.perguntas || [];
    const rules = state.motor?.regras || [];

    const mq = questions.find(x => normText(x.pergunta) === q);
    if (mq) return ruleById(mq.id);

    for (const rule of rules) {
      if ((rule.gatilhos || []).some(g => normText(g) === q)) return ruleById(rule.id);
    }
    return null;
  }

  function contextualPriority(query) {
    const q = normText(query);

    // Relação/diferença entre os dois documentos: deve vir antes de histórico digital isolado.
    if (q.includes("diploma") && q.includes("historico") &&
        (q.includes("relacao") || q.includes("diferenca") || q.includes("relaciona") || q.includes("juntos"))) {
      return ruleById("QA-011");
    }

    if (q.includes("principios da lgpd") || q.includes("tratamento de dados pessoais") || q.includes("dados pessoais")) return ruleById("QA-018");
    if (q.includes("cpf") && (q.includes("aluno") || q.includes("academic"))) return ruleById("QA-019");
    if (q.includes("acesso") && (q.includes("dados") || q.includes("academic"))) return ruleById("QA-020");
    if (q.includes("quem pode acessar") && (q.includes("dados") || q.includes("academic"))) return ruleById("QA-020");

    if ((q.includes("emissao") || q.includes("expedicao")) && q.includes("diploma") && q.includes("registro")) return ruleById("QA-002");
    if (q.includes("xml") && q.includes("diploma")) return ruleById("QA-009");
    if ((q.includes("assinatura") || q.includes("integridade") || q.includes("carimbo de tempo")) && q.includes("diploma")) return ruleById("QA-010");
    if ((q.includes("elementos tecnicos") || q.includes("estrutura tecnica") || q.includes("componentes tecnicos")) && q.includes("diploma")) return ruleById("QA-008");

    if (q.includes("historico") && q.includes("digital") && !q.includes("diploma")) return ruleById("QA-007");

    if (q.includes("preservar acervo") || (q.includes("preservacao") && q.includes("acervo"))) return ruleById("QA-015");
    if (q.includes("quem comecou antes") && q.includes("mudanca")) return ruleById("QA-045");

    if (q.includes("estagio") && q.includes("obrigatorio") && (q.includes("nao obrigatorio") || q.includes("diferenca") || q.includes("versus"))) return ruleById("QA-030");
    if (q.includes("estagio") && (q.includes("documento") || q.includes("documentacao") || q.includes("termo de compromisso"))) return ruleById("QA-031");
    if (q.includes("estagio") && (q.includes("supervisor") || q.includes("supervisao") || q.includes("orientador") || q.includes("supervisionar"))) return ruleById("QA-032");

    if (q.includes("enade") && (q.includes("historico") || q.includes("registro academico") || q.includes("regularidade no historico"))) return ruleById("QA-025");
    if (q.includes("portaria 90") || (q.includes("conceito") && q.includes("enade") && q.includes("licenciatura"))) return ruleById("QA-024");
    if (q.includes("enade") && q.includes("licenciatura")) return ruleById("QA-023");
    if (q.includes("enade") && (q.includes("cronograma") || q.includes("data") || q.includes("etapa"))) return ruleById("QA-022");
    if (q.includes("regularidade") && q.includes("enade")) return ruleById("QA-021");

    if (q.includes("censo") && (q.includes("prazo") || q.includes("cronograma") || q.includes("data"))) return ruleById("QA-028");
    if (q.includes("censo") && (q.includes("qualidade") || q.includes("consistencia") || q.includes("conferencia") || q.includes("validacao"))) return ruleById("QA-029");
    if (q.includes("e-mec") && q.includes("censo")) return ruleById("QA-027");

    if (q.includes("regulacao") && q.includes("sinaes")) return ruleById("QA-038");
    if (q.includes("regulacao") && (q.includes("fonte") || q.includes("curso") || q.includes("ies"))) return ruleById("QA-039");
    if (q.includes("regulacao") && q.includes("supervisao") && q.includes("avaliacao")) return ruleById("QA-037");

    if (q.includes("carga horaria") && (q.includes("formacao pedagogica") || q.includes("segunda licenciatura"))) return ruleById("QA-042");
    if (q.includes("segunda licenciatura")) return ruleById("QA-040");
    if (q.includes("formacao pedagogica") && q.includes("pedagogia")) return ruleById("QA-043");
    if (q.includes("formacao pedagogica") && (q.includes("graduado") || q.includes("nao licenciado"))) return ruleById("QA-041");
    if (q.includes("compatibilidade") && (q.includes("formacao") || q.includes("habilitacao"))) return ruleById("QA-044");
    if (q.includes("transicao") && (q.includes("2024") || q.includes("2025") || q.includes("norma"))) return ruleById("QA-045");
    if (q.includes("2/2019") || (q.includes("norma anterior") && q.includes("formacao"))) return ruleById("QA-046");
    if (q.includes("formacao") && q.includes("docencia") && q.includes("fonte")) return ruleById("QA-047");

    if (q.includes("conflito") && q.includes("norma")) return ruleById("QA-049");
    if ((q.includes("fundamento") || q.includes("base legal")) && (q.includes("resposta") || q.includes("oraculo"))) return ruleById("QA-048");
    if (q.includes("sem fundamento") || q.includes("fundamento insuficiente") || q.includes("nao houver fundamento")) return ruleById("QA-050");
    if (q.includes("atualizacao") && q.includes("norma")) return ruleById("QA-051");
    if (q.includes("fonte oficial") && !q.includes("formacao") && !q.includes("docencia")) return ruleById("QA-052");

    return null;
  }

  function weightedScore(query, candidate) {
    const qt = [...new Set(tokens(query))];
    const ct = new Set(tokens(candidate));
    if (!qt.length || !ct.size) return 0;

    let score = 0;
    qt.forEach(t => {
      if (ct.has(t)) score += state.weights.get(t) || 1;
    });

    const q = normText(query);
    const c = normText(candidate);
    if (q === c) score += 1000;
    else if (c.includes(q) || q.includes(c)) score += 12;

    return score;
  }

  function classify(query) {
    if (!state.ready) return null;

    const exact = exactMatch(query);
    if (exact) return exact;

    const priority = contextualPriority(query);
    if (priority) return priority;

    const rules = state.motor.regras || [];
    const questions = state.matriz.perguntas || [];
    const qMap = new Map(questions.map(q => [q.id, q]));

    const scored = rules.map(rule => {
      const candidates = [
        ...(rule.gatilhos || []),
        ...(qMap.has(rule.id) ? [qMap.get(rule.id).pergunta] : [])
      ];
      const scores = candidates.map(c => weightedScore(query, c));
      const score = Math.max(...scores, 0);
      return {rule, matrix:qMap.get(rule.id), score};
    }).filter(x => x.score > 0).sort((a,b) => b.score-a.score);

    if (!scored.length || scored[0].score < 3) return null;

    const best = scored[0];
    const second = scored[1];

    // Só declarar ambiguidade quando os dois resultados realmente têm força semelhante.
    if (second && second.score >= 3 && (best.score - second.score) < Math.max(1.5, best.score * 0.18)) {
      return {ambiguous:true, candidates:scored.slice(0,3)};
    }

    return best;
  }

  function responseFor(query) {
    const hit = classify(query);
    if (!hit) return {type:"not_found"};
    if (hit.ambiguous) return {type:"ambiguous", hit};
    const answer = state.respostas.get(hit.rule.id) || null;
    if (!answer) return {type:"fallback", hit, text:hit.rule.fallback || "Não foi localizada fundamentação suficiente na base atual para responder com segurança."};
    return {type:"validated", hit, answer};
  }

  function addPanel() {
    if ($i("oraculoAnswerPanel")) return $i("oraculoAnswerPanel");
    const radar=document.querySelector(".radar");
    if (!radar || !radar.parentNode) return null;
    const panel=document.createElement("section");
    panel.id="oraculoAnswerPanel";
    panel.className="radar hidden";
    panel.innerHTML=`
      <div class="radarhead"><div><span class="eyebrow">ORÁCULO DA SECRETARIA</span><h2 id="oraculoAnswerTitle">Resposta assistida</h2><p id="oraculoAnswerLead"></p></div><div class="radarstatus" id="oraculoAnswerStatus">Base carregada</div></div>
      <div id="oraculoAnswerBody" style="margin-top:14px"></div>`;
    radar.parentNode.insertBefore(panel,radar.nextSibling);
    return panel;
  }

  function renderValidated(result) {
    const p=addPanel(); if(!p) return;
    const a=result.answer, rule=result.hit.rule; p.classList.remove("hidden");
    $i("oraculoAnswerTitle").textContent=a.pergunta||rule.intencao||"Resposta assistida";
    $i("oraculoAnswerLead").textContent="Intenção identificada: "+rule.id+" · "+rule.processo;
    $i("oraculoAnswerStatus").textContent="Resposta validada";
    const fontes=(a.fontes||[]).map(f=>`<a href="${escI(f.url)}" target="_blank" rel="noopener">${escI(f.norma)} ↗</a>`).join(" · ");
    $i("oraculoAnswerBody").innerHTML=`<div class="section"><h4>Resposta objetiva</h4><p>${escI(a.resposta_objetiva)}</p></div><div class="section"><h4>Fundamento</h4><p>${(a.fundamento||[]).map(escI).join("<br>")}</p></div><div class="section"><h4>Impacto operacional</h4><p>${escI(a.impacto_operacional)}</p></div><div class="section"><h4>Procedimento</h4><p>${(a.procedimento||[]).map((x,i)=>(i+1)+". "+escI(x)).join("<br>")}</p></div><div class="section"><h4>Fontes</h4><p>${fontes||"Fonte cadastrada na matriz/motor."}</p></div><div class="notice">⚠️ <b>Limites:</b> ${escI(a.limites||rule.cautela||"")}<br><br><b>Governança:</b> esta resposta é orientação vinculada às fontes. A resposta validada não é citação literal da norma.</div>`;
    p.scrollIntoView({behavior:"smooth",block:"start"});
  }

  function renderFallback(result) {
    const p=addPanel(); if(!p) return; const rule=result.hit.rule; p.classList.remove("hidden");
    $i("oraculoAnswerTitle").textContent="Consulta assistida";
    $i("oraculoAnswerLead").textContent="Intenção identificada: "+rule.id+" · "+rule.processo;
    $i("oraculoAnswerStatus").textContent="Sem resposta validada";
    $i("oraculoAnswerBody").innerHTML=`<div class="notice">⚠️ <b>Resposta não validada:</b> ${escI(result.text)}</div><div class="section" style="margin-top:16px"><h4>Intenção localizada</h4><p>${escI(rule.intencao||"")}</p></div><div class="section"><h4>Referências prioritárias</h4><p>${(rule.referencias_nomes||rule.referencias_prioritarias||[]).map(escI).join(" · ")}</p></div><div class="section"><h4>Cautela</h4><p>${escI(rule.cautela||"")}</p></div>`;
    p.scrollIntoView({behavior:"smooth",block:"start"});
  }

  function renderAmbiguous(result) {
    const p=addPanel(); if(!p) return; p.classList.remove("hidden");
    $i("oraculoAnswerTitle").textContent="Precisamos delimitar a consulta";
    $i("oraculoAnswerLead").textContent="A pergunta corresponde a mais de uma intenção da matriz.";
    $i("oraculoAnswerStatus").textContent="Consulta ambígua";
    $i("oraculoAnswerBody").innerHTML=`<div class="notice">Para evitar uma resposta normativa inadequada, escolha um dos temas abaixo ou reformule a pergunta.</div><div class="section" style="margin-top:16px"><h4>Possíveis intenções</h4><p>${result.hit.candidates.map(c=>`<b>${escI(c.rule.id)}</b> — ${escI(c.rule.intencao||c.matrix?.pergunta||"")}`).join("<br>")}</p></div>`;
    p.scrollIntoView({behavior:"smooth",block:"start"});
  }

  function renderNotFound() {
    const p=addPanel(); if(!p) return; p.classList.remove("hidden");
    $i("oraculoAnswerTitle").textContent="Consulta não classificada";
    $i("oraculoAnswerLead").textContent="A camada de inteligência não encontrou uma intenção com evidência suficiente.";
    $i("oraculoAnswerStatus").textContent="Sem classificação";
    $i("oraculoAnswerBody").innerHTML=`<div class="notice">Não foi localizada fundamentação suficiente na camada de inteligência para responder com segurança. Tente detalhar o processo, documento ou norma que deseja consultar.</div>`;
    p.scrollIntoView({behavior:"smooth",block:"start"});
  }

  function run(query) {
    const result=responseFor(query);
    if(result.type==="validated") renderValidated(result); else if(result.type==="fallback") renderFallback(result); else if(result.type==="ambiguous") renderAmbiguous(result); else renderNotFound();
    return result;
  }

  function wireSearch() {
    const input = document.getElementById("q");

    if (!input) {
      console.warn("[Oráculo] Campo de pesquisa não encontrado.");
      return;
    }

    // A busca principal da V5 continua responsável pelos cards de legislação.
    // A camada de inteligência apenas expõe a execução da consulta e acompanha
    // o campo, sem substituir/clonar os controles da interface.
    window.OraculoIntelligenceRun = run;

    input.addEventListener("input", () => {
      if (!input.value.trim()) {
        const panel = document.getElementById("oraculoAnswerPanel");
        if (panel) panel.classList.add("hidden");
      }
    });
  }

  // Expor imediatamente para a busca principal da V5, mesmo enquanto a base é carregada.
  window.OraculoIntelligenceRun = run;

  async function boot(){
    const ok=await loadIntelligence();
    if(!ok){
      console.error("[Oráculo] Não foi possível inicializar a inteligência.");
      return;
    }
    wireSearch();
    addPanel();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, {once:true});
  } else {
    boot();
  }
})();
