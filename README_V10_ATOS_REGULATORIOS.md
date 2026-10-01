# Oráculo da Secretaria Acadêmica — V10
## Monitoramento específico de autorização, reconhecimento e renovação de reconhecimento

### Objetivo
Separar o monitoramento de atos regulatórios de cursos de graduação do monitoramento genérico de páginas.

### Regra central
O Oráculo deve considerar **qualquer IES**, e não apenas UNIASSELVI, UniCesumar ou Vitru.

São candidatos:
- autorização de curso de graduação;
- reconhecimento de curso de graduação;
- renovação de reconhecimento;
- aditamentos/alterações relacionados ao ato;
- outros atos que alterem formalmente a situação regulatória do curso.

### Hierarquia de fontes
1. **DOU / Imprensa Nacional** — confirmação oficial.
2. **e-MEC** — conferência cadastral/regulatória.
3. Outras fontes podem auxiliar a descoberta, mas não substituem a publicação oficial.

A Imprensa Nacional informa que o INLABS disponibiliza as publicações do DOU em XML para processamento e que esse conteúdo não substitui a versão certificada.

### O que a V10 NÃO faz
- não transforma hash de página em "norma atualizada";
- não considera uma simples ocorrência das palavras "autorização" ou "reconhecimento" como ato de curso;
- não limita a busca a uma IES;
- não grava credenciais de INLABS/e-MEC no repositório;
- não confirma um ato sem evidência suficiente.

### Arquivos
- `scripts/monitor_atos_regulatorios.py`
- `monitoring/atos_regulatorios.json`
- `monitoring/atos_regulatorios_log.json`
- `monitoring/dou_candidates.json`

### Execução local
```bash
python scripts/monitor_atos_regulatorios.py
```

### INLABS
O portal INLABS requer cadastro/login. Se a organização optar por baixar os XMLs por um processo autorizado, a pasta pode ser informada:

```bash
set INLABS_XML_DIR=C:\caminho\dos\xml
python scripts/monitor_atos_regulatorios.py
```

Em GitHub Actions, **não coloque usuário ou senha no código**. Use Secrets/Variables apenas se houver autorização institucional para automatizar o acesso.

### Consulta DOU
Por padrão, a V10 tenta a consulta pública do DOU usando:

`https://www.in.gov.br/consulta/-/buscar/dou?q={query}`

A interface do DOU pode mudar ou exigir comportamento de navegador. Por isso, a rotina registra falha de acesso em vez de inventar resultado. O parser foi desenhado para aceitar também XML do INLABS e candidatos controlados.

### Critério de confirmação
Um registro só é marcado como:
`alteracao_normativa_confirmada`

quando houver, no mínimo:
- URL oficial do DOU;
- número/ano do ato;
- evidência de curso de graduação;
- evidência de grau;
- IES identificável;
- curso identificável.

Caso contrário:
`ato_normativo_candidato` + `Validação pendente`.

### Próxima integração
A interface do Radar deve ler `monitoring/atos_regulatorios.json` e apresentar:
- Confirmados;
- Pendentes de validação;
- Tipo do ato;
- Número/ano;
- Data;
- IES;
- Curso;
- Grau;
- Modalidade;
- Local, quando explicitamente publicado;
- Link oficial do DOU;
- Link de conferência no e-MEC;
- impacto operacional para a Secretaria.

### Observação
O banco não deve ser usado como fonte primária da norma. O link oficial deve permanecer associado a cada registro.
