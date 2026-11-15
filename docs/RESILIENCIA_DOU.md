# Resiliência da coleta do DOU

## Estratégia operacional

1. **Coleta diária primária:** INLABS, por meio dos arquivos ZIP/XML das seções configuradas. O parser V10.9 continua sendo a fonte da integração de atos de curso ao Radar.
2. **Resiliência HTTP:** requisições GET repetem automaticamente falhas transitórias de conexão e respostas HTTP 429, 500, 502, 503 e 504. O login POST não é repetido automaticamente para evitar repetição de autenticação.
3. **Fallback de consulta:** o buscador público do DOU permanece como diagnóstico secundário. Seus resultados não são integrados automaticamente ao Radar e não confirmam atos.
4. **Dados Abertos:** a descoberta do catálogo é mantida como trilha de acervo histórico. A execução de 15/11/2026 identificou conjuntos anuais e recursos ZIP/XML em 2015 e 2016, mas não demonstrou cobertura de edições recentes. Portanto, esses arquivos não são usados como substitutos da coleta diária.
5. **Falha parcial:** monitoramento geral e diagnósticos continuam sendo publicados; a execução termina com falha explícita quando a coleta principal e o fallback não conseguem obter dados.

## Configuração

- `INLABS_HTTP_RETRIES`: tentativas adicionais para GET (padrão: 3).
- `INLABS_HTTP_BACKOFF`: fator de espera exponencial (padrão: 1.5).
- `DOU_HTTP_RETRIES`: tentativas adicionais do buscador público (padrão: 3).
- `DOU_HTTP_BACKOFF`: fator de espera exponencial (padrão: 1.5).

A política de retry se limita a GET. Nenhuma publicação é marcada como confirmada apenas por ter sido encontrada automaticamente. A confirmação permanece sujeita à conferência da publicação oficial.
