# Coletor alternativo do DOU — desenho técnico

## Objetivo
Adicionar uma fonte de contingência para a captura de matérias do Diário Oficial da União sem remover o INLABS e sem alterar a branch `main`.

## Evidência oficial disponível
- A Imprensa Nacional informa que as edições completas do DOU são disponibilizadas em XML no INLABS e também no Portal Brasileiro de Dados Abertos.
- A documentação oficial informa que os conjuntos do DOU são organizados por ano, mês e seção e que a catalogação no Portal de Dados Abertos pode ocorrer até o quinto dia útil do mês seguinte.
- A Imprensa Nacional informa que a pesquisa pública no portal permite localizar matérias por termos e data e consultar o conteúdo em HTML/PDF.
- O repositório oficial `Imprensa-Nacional/inlabs` descreve o INLABS como fonte diária para os últimos quatro meses; para períodos anteriores, orienta consultar as coleções do Portal de Dados Abertos.

Fontes:
- https://github.com/Imprensa-Nacional/inlabs
- https://www.gov.br/imprensanacional/pt-br/acesso-a-informacao/dados-abertos
- https://www.gov.br/imprensanacional/pt-br/arquivos/arquivos-acoes-e-programas/carta-servicos-usuario-2022.pdf
- https://www.gov.br/imprensanacional/pt-br

## Arquitetura proposta
1. **Primária — INLABS:** manter o coletor atual e sua autenticação por Secrets.
2. **Contingência — Dados Abertos:** consultar o catálogo oficial, localizar os conjuntos/recursos XML do DOU e processar os arquivos mensais disponíveis.
3. **Conferência recente — Busca pública do DOU:** quando a janela ainda não estiver publicada no catálogo de dados abertos, usar a pesquisa pública para localizar os atos por termos e data, mantendo o link oficial da matéria.
4. **Triagem:** reutilizar as funções existentes de extração e classificação; candidatos novos permanecem com `confirmado: false` e `status: "Validação pendente"`.
5. **Rastreabilidade:** registrar origem, URL do recurso, data da edição, seção, identificador da matéria e data/hora de coleta.
6. **Governança:** nunca substituir atos confirmados por dados de uma coleta incompleta; nunca interpretar indisponibilidade de fonte como ausência de publicação.

## Regra de status
O Radar deverá distinguir, no mínimo:
- `coletado_inlabs`
- `coletado_dados_abertos`
- `consulta_publica_dou`
- `fonte_indisponivel`
- `janela_ainda_nao_catalogada`

A ausência de registros só poderá ser apresentada como resultado de busca quando a fonte consultada cobrir integralmente a janela pesquisada. Se a coleta depender do catálogo mensal ainda não publicado, o status será `janela_ainda_nao_catalogada`, nunca “nenhum ato encontrado”.

## Critérios para implementação
Antes de ativar o fallback automaticamente, uma execução de descoberta deve comprovar:
- identificação do dataset oficial publicado pela Imprensa Nacional;
- localização dos recursos XML para pelo menos uma competência e seção;
- download HTTP bem-sucedido e validação de ZIP/XML;
- correspondência dos metadados (ano, mês, seção);
- extração de matérias e geração de links para a publicação oficial;
- deduplicação por identificador da matéria e/ou hash estável;
- testes automatizados com respostas simuladas e um recurso oficial real.

## Limite operacional conhecido
Os dados abertos mensais não são necessariamente publicados no mesmo dia da edição. A documentação oficial informa catalogação até o quinto dia útil do mês subsequente. Portanto, esse canal é adequado para contingência e recuperação histórica, mas não deve ser anunciado como substituto em tempo real do INLABS.

## Próxima etapa técnica
Implementar primeiro um modo `discovery` que apenas consulta o catálogo e lista datasets/recursos, sem alterar o Radar. Após comprovar o endpoint e o formato do recurso real, integrar o download ao parser existente e adicionar testes. O modo de descoberta deve ser seguro para execução diária e não pode publicar candidatos.
