from pathlib import Path
import re
import shutil
import sys

ROOT = Path.cwd()

TARGETS = [
    Path('.github/workflows/atualizacao-regulatoria.yml'),
    Path('.github/workflows/weekly-regulatory-monitor.yml'),
    Path('scripts/monitor_regulatorio.py'),
    Path('index.html'),
]

BACKUP_DIR = ROOT / '.backup_v9_1'


def fail(msg):
    raise RuntimeError(msg)


def read(rel):
    p = ROOT / rel
    if not p.exists():
        fail(f'Arquivo não encontrado: {rel}')
    return p.read_text(encoding='utf-8')


def write(rel, content):
    (ROOT / rel).write_text(content, encoding='utf-8')


def backup():
    BACKUP_DIR.mkdir(exist_ok=True)
    for rel in TARGETS:
        src = ROOT / rel
        if src.exists():
            dst = BACKUP_DIR / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def add_concurrency(s, comment):
    if 'group: oraculo-monitor-regulatorio' in s:
        return s
    marker = '  workflow_dispatch:\n\n'
    if marker not in s:
        fail('Não encontrei o bloco workflow_dispatch para inserir concurrency.')
    block = (
        marker
        + comment + '\n'
        + 'concurrency:\n'
        + '  group: oraculo-monitor-regulatorio\n'
        + '  cancel-in-progress: false\n\n'
    )
    return s.replace(marker, block, 1)


def patch_workflows():
    p = Path('.github/workflows/atualizacao-regulatoria.yml')
    s = read(p)
    s = add_concurrency(
        s,
        '# Serializa o monitor diário e o semanal para evitar dois pushes simultâneos.'
    )
    write(p, s)

    p = Path('.github/workflows/weekly-regulatory-monitor.yml')
    s = read(p)
    s = add_concurrency(
        s,
        '# Usa o mesmo estado do monitor diário; as execuções ficam serializadas.'
    )
    write(p, s)


def patch_monitor():
    rel = Path('scripts/monitor_regulatorio.py')
    s = read(rel)

    if 'def classify_change(name, url):' not in s:
        anchor = '# ============================================================\n# MONITORAR FONTES\n# ============================================================\n'
        if anchor not in s:
            fail('Não encontrei o ponto de inserção do classificador no monitor_regulatorio.py.')
        helper = r'''# ============================================================
# CLASSIFICAÇÃO CONSERVADORA DE ALTERAÇÕES
# ============================================================

def classify_change(name, url):
    """Classifica a alteração técnica sem afirmar que houve mudança normativa."""
    text = f"{name} {url}".lower()
    normative = re.search(
        r"\b(portaria|resolu[cç][aã]o|decreto|lei|parecer|"
        r"instru[cç][aã]o\s+normativa|delibera[cç][aã]o)\b",
        text,
    )
    guidance = re.search(
        r"(faq|perguntas|orienta[cç][aã]o|not[ií]cia|news|"
        r"forma[cç][aã]o|cobran[cç]a|d[uú]vidas|como-funciona|"
        r"educa[cç][aã]o.*dist[aâ]ncia)",
        text,
    )

    if normative:
        return {
            "change_type": "ato_normativo_candidato",
            "verification_status": "validacao_pendente",
            "confirmed_normative_change": False,
            "label": "ATO NORMATIVO — VALIDAÇÃO PENDENTE",
        }

    if guidance:
        return {
            "change_type": "orientacao_pagina",
            "verification_status": "sem_confirmacao_normativa",
            "confirmed_normative_change": False,
            "label": "ORIENTAÇÃO/PÁGINA OFICIAL ATUALIZADA",
        }

    return {
        "change_type": "pagina_oficial",
        "verification_status": "sem_confirmacao_normativa",
        "confirmed_normative_change": False,
        "label": "PÁGINA OFICIAL ATUALIZADA",
    }


'''
        s = s.replace(anchor, helper + anchor, 1)

    # Corrige o bug da V9 original: a presença da função acima não pode impedir
    # a inclusão da classificação dentro do bloco changes.append().
    old = '''        changes.append({
            "name": name,
            "url": url,
            "previous_hash": previous_hash,
            "current_hash": content_hash,
        })'''
    new = '''        classification = classify_change(name, url)

        changes.append({
            "name": name,
            "url": url,
            "previous_hash": previous_hash,
            "current_hash": content_hash,
            **classification,
        })'''
    if old in s:
        s = s.replace(old, new, 1)
    elif 'classification = classify_change(name, url)' not in s:
        fail('Não encontrei o bloco changes.append() esperado no monitor_regulatorio.py.')

    # Contagens semânticas: alteração de hash continua separada de confirmação normativa.
    if 'classification_counts = {' not in s:
        marker = 'result_data = {\n    "checked_at": checked_at,'
        if marker not in s:
            fail('Não encontrei o ponto de inserção das contagens semânticas.')
        counts = '''classification_counts = {
    "ato_normativo_candidato": sum(
        1 for x in changes
        if x.get("change_type") == "ato_normativo_candidato"
    ),
    "orientacao_pagina": sum(
        1 for x in changes
        if x.get("change_type") == "orientacao_pagina"
    ),
    "pagina_oficial": sum(
        1 for x in changes
        if x.get("change_type") == "pagina_oficial"
    ),
    "alteracao_normativa_confirmada": sum(
        1 for x in changes
        if x.get("confirmed_normative_change") is True
    ),
}

'''
        s = s.replace(marker, counts + marker, 1)

    # Acrescenta as contagens ao result_data.
    if '"classification_counts": classification_counts' not in s:
        marker = '    "changes": changes,\n'
        if marker not in s:
            fail('Não encontrei o campo changes no result_data.')
        s = s.replace(
            marker,
            '    "classification_counts": classification_counts,\n\n' + marker,
            1,
        )

    # Manifesto: preserva compatibilidade e acrescenta os novos indicadores.
    marker = '    "last_weekly_changes_detected": len(changes),\n'
    if '"last_weekly_normative_candidates"' not in s:
        if marker not in s:
            fail('Não encontrei o campo last_weekly_changes_detected no manifest.')
        s = s.replace(
            marker,
            marker
            + '    "last_weekly_normative_candidates": classification_counts["ato_normativo_candidato"],\n'
            + '    "last_weekly_normative_confirmed": classification_counts["alteracao_normativa_confirmada"],\n',
            1,
        )

    # Relatórios e logs passam a usar linguagem tecnicamente correta.
    s = s.replace(
        'f"- Alterações detectadas: **{len(changes)}**"',
        'f"- Alterações de conteúdo detectadas: **{len(changes)}**"',
    )
    s = s.replace(
        '"## 🔎 Alterações detectadas"',
        '"## 🔎 Alterações de conteúdo detectadas"',
    )
    s = s.replace(
        'f"🔎 Alterações detectadas: {len(changes)}"',
        'f"🔎 Alterações de conteúdo detectadas: {len(changes)}"',
    )

    write(rel, s)


def replace_between(s, start, end, replacement, label):
    a = s.find(start)
    if a < 0:
        fail(f'Âncora inicial não encontrada: {label}')
    b = s.find(end, a + len(start))
    if b < 0:
        fail(f'Âncora final não encontrada: {label}')
    return s[:a] + replacement + s[b:]


def patch_index():
    rel = Path('index.html')
    s = read(rel)

    s = s.replace(
        '<div class="stat"><b id="statUpdates">0</b><span>atualizações detectadas</span></div>',
        '<div class="stat"><b id="statUpdates">0</b><span>alterações de conteúdo detectadas</span></div>',
    )

    build = '''function buildUpdates(m){
if(!m)return[];
const changes=Array.isArray(m.changes)?m.changes:[];
const labels={
  ato_normativo_candidato:'ATO NORMATIVO — VALIDAÇÃO PENDENTE',
  orientacao_pagina:'ORIENTAÇÃO/PÁGINA OFICIAL ATUALIZADA',
  pagina_oficial:'PÁGINA OFICIAL ATUALIZADA'
};
return changes.map((c,i)=>{
  const x=DB.find(x=>x.id===c.id)||DB.find(x=>x.source===c.url)||{};
  const impact=(x.risk||'Médio')==='Alto'?'action':(x.risk||'Médio')==='Médio'?'attention':'info';
  const kind=c.change_type||'pagina_oficial';
  return {
    id:'change-'+i,
    normId:x.id||c.id||'',
    norm:x.norm||c.name,
    category:x.category||'Fonte oficial',
    date:x.date||'',
    type:labels[kind]||'ALTERAÇÃO DE CONTEÚDO — VALIDAÇÃO PENDENTE',
    changeType:kind,
    verificationStatus:c.verification_status||'validacao_pendente',
    confirmedNormativeChange:c.confirmed_normative_change===true,
    focus:x.mainFocus||x.summary||'A fonte oficial apresentou alteração de conteúdo desde a última verificação.',
    where:x.changeLocation||x.article||'Confirmar no conteúdo oficial onde ocorreu a alteração.',
    impactLabel:x.operationalImpact||x.impact||'Avaliar impacto na Secretaria Acadêmica somente após validação.',
    action:x.recommendedAction||x.action||x.procedure||'Consultar a fonte oficial e validar se houve alteração normativa antes de atualizar procedimento.',
    risk:impact,
    source:x.source||c.url||'',
    process:x.process||'Regulação'
  };
});
}
'''
    if 'function buildUpdates(m){' in s:
        s = replace_between(s, 'function buildUpdates(m){', 'function renderRadar(){', build, 'buildUpdates')
    else:
        fail('Não encontrei buildUpdates no index.html.')

    radar = '''function renderRadar(){
const box=$('updateCards');if(!box)return;
const count=UPDATES.length;
const confirmed=UPDATES.filter(x=>x.confirmedNormativeChange).length;
const candidates=UPDATES.filter(x=>x.changeType==='ato_normativo_candidato').length;
$('statUpdates').textContent=count;
$('radarStatus').textContent=count?'⚠️ '+count+' alteração'+(count===1?'':'ões')+' de conteúdo — '+confirmed+' normativa'+(confirmed===1?' confirmada':'s confirmadas'):'✓ Nenhuma alteração de conteúdo detectada';
const checked=MONITOR?.checked_at;
if(checked)$('lastCheck').textContent='Última verificação: '+new Date(checked).toLocaleString('pt-BR');
if(!count){
  box.innerHTML='<div class="updateempty"><b>Sem novas alterações de conteúdo na última verificação.</b><br>O monitoramento continua acompanhando as fontes oficiais.</div>';
  return;
}
box.innerHTML='<div style="grid-column:1/-1;background:#fff8e6;border:1px solid #f0d68a;border-radius:10px;padding:10px;font-size:11px;color:#6e5311"><b>Governança:</b> '+count+' alterações de conteúdo foram detectadas. '+candidates+' fonte(s) parecem apontar para ato normativo, mas nenhuma alteração normativa é considerada confirmada automaticamente. Valide o ato oficial antes de atualizar procedimentos.</div>'+UPDATES.slice(0,3).map(updateCard).join('');
document.querySelectorAll('[data-update-open]').forEach(b=>b.onclick=()=>openUpdateModal(UPDATES.find(x=>x.id===b.dataset.updateOpen)));
}
'''
    s = replace_between(s, 'function renderRadar(){', 'function updateCard(x){', radar, 'renderRadar')

    card = '''function updateCard(x){
return '<article class="updatecard"><div class="utop"><span class="utype">'+esc(x.type)+'</span><span class="uimpact '+esc(x.risk)+'">'+esc(x.confirmedNormativeChange?'NORMA CONFIRMADA':x.changeType==='ato_normativo_candidato'?'VALIDAÇÃO PENDENTE':'INFORMATIVO')+'</span></div><h3>'+esc(x.norm)+'</h3><div class="umeta">'+esc(x.category)+' · '+esc(x.process)+'</div><div class="uchange"><b>O que mudou?</b><br>Foi detectada alteração de conteúdo na fonte. Isso não equivale, por si só, a uma norma nova ou alterada.</div><p><b>Principal foco:</b> '+esc(x.focus)+'</p><p><b>Onde olhar:</b> '+esc(x.where)+'</p><p><b>Status da validação:</b> '+esc(x.confirmedNormativeChange?'Alteração normativa confirmada':x.verificationStatus==='validacao_pendente'?'Validação normativa pendente':'Sem confirmação normativa')+'</p><p><b>Impacto:</b> '+esc(x.impactLabel)+'</p><div class="uaction">Ação: '+esc(x.action)+'</div><div class="actions"><button class="primary" data-update-open="'+esc(x.id)+'">Ver análise</button><a href="'+esc(x.source)+'" target="_blank" rel="noopener">Fonte oficial ↗</a></div></article>';
}
'''
    s = replace_between(s, 'function updateCard(x){', 'function openUpdateModal(x){', card, 'updateCard')

    s = s.replace(
        '<div class="notice">⚠️ <b>Importante:</b> o monitoramento identifica alteração na fonte. A classificação do que mudou e o impacto operacional devem ser validados no ato oficial antes de virar procedimento.</div>',
        '<div class="notice">⚠️ <b>Importante:</b> o monitoramento detecta alteração de conteúdo por comparação técnica. Somente a validação do ato oficial deve confirmar criação, alteração, revogação ou outra mudança normativa.</div>',
    )
    s = s.replace(
        "<h4>Atualizações detectadas</h4><p>Foram identificadas '+UPDATES.length+' alterações nas fontes monitoradas. Valide cada ato antes de atualizar um procedimento interno.</p>",
        "<h4>Alterações de conteúdo detectadas</h4><p>Foram identificadas '+UPDATES.length+' alterações nas fontes monitoradas. Isso não significa que '+UPDATES.length+' normas tenham sido atualizadas. Valide cada ato antes de atualizar um procedimento interno.</p>",
    )

    write(rel, s)


def validate():
    required = {
        Path('scripts/monitor_regulatorio.py'): [
            'def classify_change(name, url):',
            '"change_type": "ato_normativo_candidato"',
            'classification_counts = {',
        ],
        Path('.github/workflows/atualizacao-regulatoria.yml'): [
            'group: oraculo-monitor-regulatorio',
        ],
        Path('.github/workflows/weekly-regulatory-monitor.yml'): [
            'group: oraculo-monitor-regulatorio',
        ],
        Path('index.html'): [
            'alterações de conteúdo detectadas',
            'function buildUpdates(m){',
            'function renderRadar(){',
            'confirmedNormativeChange',
        ],
    }
    for rel, needles in required.items():
        s = read(rel)
        for needle in needles:
            if needle not in s:
                fail(f'Validação falhou em {rel}: trecho ausente: {needle}')

    # Confirma que o monitor não marca automaticamente uma mudança como confirmada.
    monitor = read(Path('scripts/monitor_regulatorio.py'))
    if '"confirmed_normative_change": True' in monitor:
        fail('Validação falhou: o monitor contém confirmação normativa automática.')

    print('VALIDAÇÃO V9.1 OK')
    print('Arquivos ajustados:')
    for rel in TARGETS:
        print(f' - {rel}')
    print(f'Backup: {BACKUP_DIR}')


def main():
    if not (ROOT / '.git').exists():
        fail('Execute este script na raiz do clone do repositório Oráculo (onde existe a pasta .git).')

    print('Oráculo — aplicação dos ajustes V9.1')
    backup()
    patch_workflows()
    patch_monitor()
    patch_index()
    validate()
    print('\nAjustes aplicados sem alterar data.json, monitoring/latest.json, snapshots ou reports diretamente.')
    print('Agora revise com: git diff')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERRO: {exc}', file=sys.stderr)
        print('Nenhuma publicação no GitHub é feita por este script.', file=sys.stderr)
        sys.exit(1)
