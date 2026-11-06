# Oráculo da Secretaria Acadêmica — V10.9

## Monitoramento de atos regulatórios de cursos

### Objetivo
Monitorar atos regulatórios publicados oficialmente que alterem a situação de cursos de graduação de qualquer Instituição de Ensino Superior (IES).

### Atos monitorados
- Autorização de cursos.
- Reconhecimento de cursos.
- Renovação de reconhecimento.
- Aditamentos e alterações relacionados aos atos.
- Outros atos que modifiquem formalmente a situação regulatória do curso.

### Hierarquia de fontes
1. Diário Oficial da União (DOU) / Imprensa Nacional.
2. e-MEC para conferência cadastral e regulatória.
3. Outras fontes oficiais como apoio à identificação.

### Melhorias da versão V10.9

- Janela padrão de captura de sete datas corridas, incluindo hoje.
- Referência ao fuso horário America/Sao_Paulo.
- Integração dos atos identificados ao Radar do Oráculo.
- Leitura estruturada de tabelas XML do DOU.
- Extração de IES, curso, grau, modalidade, código e-MEC e vagas.
- Preservação dos atos já confirmados.
- Novos registros permanecem como Validação pendente.
- Interface consulta os arquivos latest.json e atos_regulatorios.json.
- Workflow único para execução diária e manual.
- Testes automatizados para validação do parser.

### Arquivos principais

- scripts/monitor_atos_regulatorios.py
- scripts/test_monitor_atos_regulatorios.py
- monitoring/atos_regulatorios.json
- monitoring/atos_regulatorios_log.json
- monitoring/dou_candidates.json

### Configuração do INLABS

No GitHub, acesse:

Settings → Secrets and variables → Actions

Os seguintes Secrets devem estar cadastrados:

- INLABS_EMAIL
- INLABS_PASSWORD

Não coloque credenciais diretamente no código.

### Execução

O workflow principal é:

.github/workflows/atualizacao-regulatoria.yml

A execução está programada diariamente às 09h, horário de Brasília, e também pode ser iniciada manualmente em:

Actions → Atualização Regulatória Diária do Oráculo → Run workflow.

### Critério de validação

Todo novo ato identificado deve permanecer como:

confirmado: false

status: Validação pendente

A confirmação depende de análise da publicação oficial e de evidências suficientes para identificar o ato e o curso relacionado.

### Governança

O Oráculo não deve apresentar candidatos como atos confirmados.

Registros anteriormente confirmados devem ser preservados durante as atualizações.

O DOU permanece como referência oficial para validação dos atos.
