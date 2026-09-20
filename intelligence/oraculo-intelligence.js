/*
 * Oráculo da Secretaria Acadêmica — camada de inteligência V1
 * Integração não destrutiva: não altera o CSS/base visual da V5.
 * Fontes de dados:
 *   intelligence/matriz_perguntas.json
 *   intelligence/motor_resposta.json
 *   intelligence/respostas_validadas.json
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
    ready: false
  };

  const $i = id => document.getElementById(id);
  const normText = value => String(value ?? "")
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, " ")
    .replace(/\s+/g, " ").trim();

  const escI = value => String(value ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));

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
      (respostas.respostas || []).forEach(r => state.respostas.set(r.pergunta_id, r));
      state.ready = true;
      window.OraculoIntelligence = state;
      console.info("[Oráculo] Inteligência carregada:", {
        perguntas: (matriz.perguntas || []).length,
        regras: (motor.regras || []).length,
        respostasValidadas: state.respostas.size
      });
      return true;
    } catch (err) {
      console.error("[Oráculo] Falha ao carregar camada de inteligência:", err);
      state.ready = false;
      return false;
    }
  }

function classifyNorm(value) {
  const text = normText(value);

  const synonyms = {
    "expedicao": "emissao",
    "expedir": "emitir",
    "expedido": "emitido",
    "expedidos": "emitidos",
    "diplomas": "diploma",
    "registros": "registro",
    "licenciaturas": "licenciatura",
    "cursos": "curso",
    "alunos": "aluno",
    "documentos": "documento",
    "assinaturas": "assinatura",
    "historicos": "historico"
  };

  const stopwords = new Set([
    "a","o","as","os",
    "de","da","do","das","dos",
    "em","no","na","nos","nas",
    "para","por","com",
    "e","ou",
    "um","uma","uns","umas",
    "que","qual","quais",
    "como","onde","quando",
    "posso","pode","podem",
    "devo","devem",
    "preciso","precisa",
    "sobre","me","se"
  ]);

  return text
    .split(/\s+/)
    .map(t => synonyms[t] || t)
    .filter(t => t.length > 2 && !stopwords.has(t));
}

function phraseScore(query, candidate) {
  const q = normText(query);
  const c = normText(candidate);

  if (!q || !c) return 0;

  if (q === c) return 100;

  if (q.includes(c)) return 25;

const strongPhrases = [
  ["principios da lgpd", ["dados pessoais"]],
  ["tratamento de dados pessoais", ["dados pessoais"]],
  ["dados pessoais", ["dados pessoais"]],
  ["lei geral de protecao de dados", ["dados pessoais"]],

  ["expedicao e registro", ["emissao diploma", "registro diploma"]],
  ["expedicao registro diploma", ["emissao diploma"]],
  ["elementos tecnicos", ["estrutura tecnica"]],
  ["estrutura tecnica", ["estrutura tecnica"]],
  ["componentes tecnicos", ["estrutura tecnica"]],
  ["estrutura do diploma digital", ["estrutura tecnica"]],

  ["xml", ["xml"]],
  ["arquivo xml", ["xml"]],
  ["estrutura xml", ["xml"]],

  ["assinatura", ["assinatura"]],
  ["assinatura digital", ["assinatura"]],
  ["carimbo de tempo", ["assinatura"]],

  ["historico escolar digital", ["historico digital"]],
  ["segunda licenciatura", ["segunda licenciatura"]],
  ["formacao pedagogica", ["formacao pedagogica"]],
  ["estagio obrigatorio", ["estagio"]],
  ["estagio nao obrigatorio", ["estagio"]]
];
  for (const [phrase, targets] of strongPhrases) {
    if (q.includes(phrase) && targets.some(t => c.includes(t))) {
      return 40;
    }
  }

  return 0;
}

function tokenScore(query, candidate) {
  const qt = classifyNorm(query);
  const ct = classifyNorm(candidate);

  if (!qt.length || !ct.length) return 0;

  const cSet = new Set(ct);

  let score = 0;

  qt.forEach(token => {
    if (cSet.has(token)) {
      score += 5;
    }
  });

  const qText = qt.join(" ");
  const cText = ct.join(" ");

  if (qText === cText) {
    score += 20;
  }

  if (cText.includes(qText) || qText.includes(cText)) {
    score += 8;
  }

  return score;
}

function classify(query) {
  if (!state.ready) return null;

  const rules = state.motor.regras || [];
  const questions = state.matriz.perguntas || [];
  const qMap = new Map(questions.map(q => [q.id, q]));

  const scored = rules.map(rule => {
    const matrix = qMap.get(rule.id);

    const candidates = [
      ...(rule.gatilhos || []),
      ...(matrix ? [matrix.pergunta] : [])
    ];

    let score = 0;

    candidates.forEach(candidate => {
      score = Math.max(
        score,
        phraseScore(query, candidate),
        tokenScore(query, candidate)
      );
    });

    return {
      rule,
      matrix,
      score
    };
  })
  .filter(x => x.score > 0)
  .sort((a, b) => b.score - a.score);

  if (!scored.length) return null;

  const best = scored[0];
  const second = scored[1];

  if (best.score < 5) return null;

  if (
    second &&
    second.score >= 5 &&
    best.score === second.score
  ) {
    return {
      ambiguous: true,
      candidates: scored.slice(0, 3)
    };
  }

  return best;
}
  function responseFor(query) {
    const hit = classify(query);
    if (!hit) return {type:"not_found"};
    if (hit.ambiguous) return {type:"ambiguous", hit};

    const answer = state.respostas.get(hit.rule.id) || null;
    if (!answer) {
      return {
        type:"fallback",
        hit,
        text: hit.rule.fallback || "Não foi localizada fundamentação suficiente na base atual para responder com segurança."
      };
    }
    return {type:"validated", hit, answer};
  }

  function addPanel() {
    if ($i("oraculoAnswerPanel")) return $i("oraculoAnswerPanel");
    const radar = document.querySelector(".radar");
    const panel = document.createElement("section");
    panel.id = "oraculoAnswerPanel";
    panel.className = "radar hidden";
    panel.innerHTML = `
      <div class="radarhead">
        <div>
          <span class="eyebrow">ORÁCULO DA SECRETARIA</span>
          <h2 id="oraculoAnswerTitle">Resposta assistida</h2>
          <p id="oraculoAnswerLead"></p>
        </div>
        <div class="radarstatus" id="oraculoAnswerStatus">Base carregada</div>
      </div>
      <div id="oraculoAnswerBody" style="margin-top:14px"></div>
    `;
    radar.parentNode.insertBefore(panel, radar.nextSibling);
    return panel;
  }

  function renderValidated(result) {
    const p = addPanel();
    const a = result.answer;
    const rule = result.hit.rule;
    p.classList.remove("hidden");
    $i("oraculoAnswerTitle").textContent = a.pergunta || rule.intencao || "Resposta assistida";
    $i("oraculoAnswerLead").textContent = "Intenção identificada: " + rule.id + " · " + rule.processo;
    $i("oraculoAnswerStatus").textContent = "Resposta validada";
    const fontes = (a.fontes || []).map(f =>
      `<a href="${escI(f.url)}" target="_blank" rel="noopener">${escI(f.norma)} ↗</a>`
    ).join(" · ");
    $i("oraculoAnswerBody").innerHTML = `
      <div class="section"><h4>Resposta objetiva</h4><p>${escI(a.resposta_objetiva)}</p></div>
      <div class="section"><h4>Fundamento</h4><p>${(a.fundamento||[]).map(escI).join("<br>")}</p></div>
      <div class="section"><h4>Impacto operacional</h4><p>${escI(a.impacto_operacional)}</p></div>
      <div class="section"><h4>Procedimento</h4><p>${(a.procedimento||[]).map((x,i)=>(i+1)+". "+escI(x)).join("<br>")}</p></div>
      <div class="section"><h4>Fontes</h4><p>${fontes || "Fonte cadastrada na matriz/motor."}</p></div>
      <div class="notice">⚠️ <b>Limites:</b> ${escI(a.limites || rule.cautela || "")}
      <br><br><b>Governança:</b> esta resposta é orientação vinculada às fontes. A resposta validada não é citação literal da norma.</div>
    `;
    p.scrollIntoView({behavior:"smooth", block:"start"});
  }

  function renderFallback(result) {
    const p = addPanel();
    const rule = result.hit.rule;
    p.classList.remove("hidden");
    $i("oraculoAnswerTitle").textContent = "Consulta assistida";
    $i("oraculoAnswerLead").textContent = "Intenção identificada: " + rule.id + " · " + rule.processo;
    $i("oraculoAnswerStatus").textContent = "Sem resposta validada";
    $i("oraculoAnswerBody").innerHTML = `
      <div class="notice">⚠️ <b>Resposta não validada:</b> ${escI(result.text)}</div>
      <div class="section" style="margin-top:16px"><h4>Intenção localizada</h4><p>${escI(rule.intencao || "")}</p></div>
      <div class="section"><h4>Referências prioritárias</h4><p>${(rule.referencias_nomes || rule.referencias_prioritarias || []).map(escI).join(" · ")}</p></div>
      <div class="section"><h4>Cautela</h4><p>${escI(rule.cautela || "")}</p></div>
    `;
    p.scrollIntoView({behavior:"smooth", block:"start"});
  }

  function renderAmbiguous(result) {
    const p = addPanel();
    p.classList.remove("hidden");
    $i("oraculoAnswerTitle").textContent = "Precisamos delimitar a consulta";
    $i("oraculoAnswerLead").textContent = "A pergunta corresponde a mais de uma intenção da matriz.";
    $i("oraculoAnswerStatus").textContent = "Consulta ambígua";
    $i("oraculoAnswerBody").innerHTML = `
      <div class="notice">Para evitar uma resposta normativa inadequada, escolha um dos temas abaixo ou reformule a pergunta.</div>
      <div class="section" style="margin-top:16px">
        <h4>Possíveis intenções</h4>
        <p>${result.hit.candidates.map(c => `<b>${escI(c.rule.id)}</b> — ${escI(c.rule.intencao || c.matrix?.pergunta || "")}`).join("<br>")}</p>
      </div>`;
    p.scrollIntoView({behavior:"smooth", block:"start"});
  }

  function renderNotFound() {
    const p = addPanel();
    p.classList.remove("hidden");
    $i("oraculoAnswerTitle").textContent = "Consulta não classificada";
    $i("oraculoAnswerLead").textContent = "A camada de inteligência não encontrou uma intenção com evidência suficiente.";
    $i("oraculoAnswerStatus").textContent = "Sem classificação";
    $i("oraculoAnswerBody").innerHTML = `
      <div class="notice">Não foi localizada fundamentação suficiente na camada de inteligência para responder com segurança. Tente detalhar o processo, documento ou norma que deseja consultar.</div>`;
    p.scrollIntoView({behavior:"smooth", block:"start"});
  }

  function run(query) {
    const result = responseFor(query);
    if (result.type === "validated") renderValidated(result);
    else if (result.type === "fallback") renderFallback(result);
    else if (result.type === "ambiguous") renderAmbiguous(result);
    else renderNotFound();
    return result;
  }

  function wireSearch() {
    const oldSearch = document.getElementById("searchBtn");
    const oldInput = document.getElementById("q");
    if (!oldSearch || !oldInput) return;

    // Clona os controles para remover os listeners antigos da V5 sem tocar no layout.
    const search = oldSearch.cloneNode(true);
    const input = oldInput.cloneNode(true);
    oldSearch.replaceWith(search);
    oldInput.replaceWith(input);

    search.onclick = () => {
      const query = input.value.trim();
      if (!query) return;
      run(query);
      // Mantém também a pesquisa documental da V5 abaixo do painel.
      window.scrollTo({top: Math.max(0, document.querySelector(".resultsbar").getBoundingClientRect().top + window.scrollY - 20), behavior:"smooth"});
    };

    input.addEventListener("keydown", e => {
      if (e.key === "Enter") {
        e.preventDefault();
        search.click();
      }
    });

    document.querySelectorAll(".quick button").forEach(btn => {
      const clone = btn.cloneNode(true);
      btn.replaceWith(clone);
      clone.onclick = () => {
        input.value = clone.dataset.q || "";
        search.click();
      };
    });

    // Mantém a consulta documental ao filtrar referências, mas agora também abre a resposta assistida.
    window.OraculoIntelligenceRun = run;
  }

  async function boot() {
    const ok = await loadIntelligence();
    if (!ok) return;
    wireSearch();
    addPanel();
  }

  window.addEventListener("load", boot);
})();
