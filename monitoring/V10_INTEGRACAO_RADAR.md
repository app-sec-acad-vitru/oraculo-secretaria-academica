# Contrato de integração do Radar — V10

A interface existente deve consumir `monitoring/atos_regulatorios.json`.

Sugestão de blocos no Radar:

1. **Atos regulatórios confirmados**
   - mostrar somente `confirmado == true`.

2. **Candidatos para validação**
   - mostrar `confirmado == false`.

3. **Campos**
   - `tipo_ato`
   - `numero_ato`
   - `ano`
   - `data_publicacao`
   - `ies`
   - `mantenedora`
   - `curso`
   - `grau`
   - `modalidade`
   - `local`
   - `situacao`
   - `fonte_oficial`
   - `fonte_conferencia`

4. **Regra visual**
   - Confirmado: "ATO REGULATÓRIO CONFIRMADO"
   - Pendente: "ATO NORMATIVO — VALIDAÇÃO PENDENTE"

5. **Importante**
   O bloco não deve usar `changes_detected` do monitor genérico como sinônimo de novos atos.
