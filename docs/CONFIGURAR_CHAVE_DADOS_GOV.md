# Configuração da chave da API do Portal de Dados Abertos

A especificação OpenAPI oficial em https://dados.gov.br/v3/api-docs declara autenticação global pelo cabeçalho `Authorization`. A própria especificação orienta obter a chave no perfil **Consumidor** ou **Gestor de Políticas de Dados Abertos**, entrando no sistema e acessando **Minha Conta**.

## Configuração no GitHub

1. Entre no Portal Brasileiro de Dados Abertos: https://dados.gov.br/
2. Acesse com o perfil indicado pela documentação oficial (Consumidor ou Gestor de Políticas de Dados Abertos).
3. Abra **Minha Conta** e localize a opção de chave de API.
4. Gere/copiei a chave conforme as instruções exibidas pelo portal. Não a envie por e-mail, chat ou commit.
5. No repositório GitHub, acesse **Settings → Secrets and variables → Actions → New repository secret**.
6. Cadastre o nome exatamente como `DADOS_GOV_API_TOKEN` e cole o valor da chave.
7. Salve e execute manualmente o workflow na branch `docs/coletor-dou-dados-abertos`.

## Comportamento esperado

- Sem Secret: relatório `autenticacao_necessaria`; nenhuma chamada é feita; o workflow principal continua.
- Chave recusada (HTTP 401/403): relatório `autenticacao_rejeitada`; nenhuma alteração no Radar.
- Consulta autorizada: o script lista conjuntos e recursos XML/ZIP reconhecidos. Nesta etapa não baixa arquivos, não executa parser e não cria candidatos.

## Endpoint documentado

- OpenAPI: https://dados.gov.br/v3/api-docs
- Listagem: `GET https://dados.gov.br/dados/api/publico/conjuntos-dados`
- Parâmetros documentados: `nomeConjuntoDados`, `dadosAbertos`, `isPrivado` e `pagina`.
- A chave é enviada no cabeçalho `Authorization`.

## Segurança

A chave deve existir somente como GitHub Actions Secret. Não incluir seu conteúdo em logs, código-fonte, artefatos ou capturas de tela. O script não imprime o cabeçalho nem a chave.
