# Correções do Oráculo da Secretaria Acadêmica — V10.9

## 1. Objetivo da atualização

Consolidar o monitoramento regulatório em um único fluxo de execução, melhorar a captura de atos publicados no Diário Oficial da União e integrar os resultados ao Radar do Oráculo.

## 2. Correções implementadas

### 2.1. Monitoramento
- Execução diária e manual.
- Janela de captura de sete datas corridas.
- Consideração do fuso horário de Brasília.
- Integração do monitoramento geral com o coletor de atos regulatórios.

### 2.2. Coletor DOU
- Melhor interpretação de tabelas XML.
- Extração de IES e curso.
- Identificação de grau e modalidade.
- Extração de código e-MEC e vagas.
- Registro de evidências e confiança da identificação.

### 2.3. Radar regulatório
- Integração com monitoring/atos_regulatorios.json.
- Exibição de atos identificados na interface.
- Diferenciação entre atos confirmados e pendentes.
- Preservação de registros anteriormente confirmados.

### 2.4. Segurança da validação
Novos candidatos não são confirmados automaticamente.

Todo novo registro deve permanecer com:

confirmado: false

status: Validação pendente

A confirmação depende de análise da publicação oficial.

### 2.5. Workflow
Foi consolidado o fluxo principal em:

.github/workflows/atualizacao-regulatoria.yml

Os workflows antigos foram descontinuados para evitar execuções duplicadas.

## 3. Arquivos atualizados

- index.html
- scripts/monitor_atos_regulatorios.py
- .github/workflows/atualizacao-regulatoria.yml
- README_V10_ATOS_REGULATORIOS.md

## 4. Arquivos adicionados

- scripts/test_monitor_atos_regulatorios.py
- CORRECOES_V10_9.md

## 5. Configuração necessária

Cadastrar os seguintes Secrets no GitHub:

INLABS_EMAIL

INLABS_PASSWORD

Caminho:

Settings → Secrets and variables → Actions

## 6. Validações técnicas realizadas no pacote

- Compilação Python.
- Verificação sintática dos scripts JavaScript.
- Três testes automatizados aprovados.
- Teste de integração com dados simulados.
- Verificação estrutural do pacote ZIP.

## 7. Próxima etapa

Executar o workflow no GitHub Actions e validar a coleta real do INLABS, a geração dos arquivos de monitoramento e a exibição dos atos no Radar.

A coleta real depende das credenciais INLABS configuradas no repositório.
