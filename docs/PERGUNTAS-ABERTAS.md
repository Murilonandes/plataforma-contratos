# Perguntas abertas

> Decisões pendentes da plataforma de contratos, agrupadas por quem responde. Numeração igual à do
> ARCHITECTURE §14 e aos `TODO(decisão #N)` do código. Atualizado em 2026-09-24 (fim da Fase 1).
>
> Cada item traz a pergunta objetiva, o contexto, o **default atual no código** (o que acontece hoje
> se ninguém responder) e onde isso vive. Quando uma resposta chegar: atualizar o código/teste, o §14
> do ARCHITECTURE e remover o item daqui.

## Sysfértil

A Sysfértil desenvolveu o serviço `ZAPI_CONTRATO_VENDAS`. Os itens #3, #4 e #9 também se resolvem
no 1º POST real em DEV; perguntar antes evita tentativa e erro.

### #5 — MaxLength real de `NotaInternaCli`, `PedidoSysFertil`, `Culture` e `LongText`
- **Pergunta:** qual o tamanho máximo aceito em cada um desses quatro campos?
- **Contexto:** o `$metadata` não declara `MaxLength` para eles. Sem limite, um texto longo chega ao
  SAP e pode ser truncado ou rejeitado com erro pouco claro.
- **Default atual:** teto provisório de **255** em `NotaInternaCli`, `PedidoSysFertil` e `Culture`;
  **1000** em `LongText`. Acima disso o domínio devolve `max_length`.
- **Onde:** `backend/app/domain/contract.py` (`_TETO_PROVISORIO`, `TEXTO`).

### #3 — Formato da resposta 201 e das mensagens de erro
- **Pergunta:** qual o corpo exato da resposta 201 (onde vem o número do contrato) e o formato de
  `error.details` / header `sap-messages` nos erros de negócio?
- **Contexto:** o worker classifica o resultado do POST e mostra as mensagens ao vendedor. Sem o
  formato real, o parser da Fase 2 é escrito às cegas.
- **Default atual:** nada implementado ainda (parser é Fase 2). O domínio espera o número em
  `SalesContract` para a transição `SAP_201`.
- **Onde:** Fase 2 (`app/infrastructure/sap/`); ARCHITECTURE §7.

### #4 — Decimal como número ou como string (`IEEE754Compatible`)
- **Pergunta:** o serviço aceita `Content-Type: application/json;IEEE754Compatible=true` com
  decimais como string (`"7766.52"`)? E como número (`7766.52`)?
- **Contexto:** string evita qualquer risco de `float` no caminho. O mapper já suporta os dois modos.
- **Default atual:** flag `decimal_as_string` no mapper, passada por parâmetro; nos dois modos o
  decimal sai com a escala do campo fixada. O valor padrão da config (`SAP_DECIMAL_AS_STRING`) entra
  na Fase 2, proposto `true`.
- **Onde:** `backend/app/infrastructure/sap/mapper.py`; ARCHITECTURE §7.

### #9 — `Edm.Date` ausente: omitir a chave ou mandar `null`?
- **Pergunta:** se uma data opcional não for informada (ex.: `CustomerPurchaseOrderDate`,
  `ScheduleDate2`), o serviço aceita a chave omitida? Ou exige `null` explícito?
- **Contexto:** string vazia `""` só vale para `Edm.String`; em data seria erro de tipo.
- **Default atual:** a chave é **omitida** do payload.
- **Onde:** `backend/app/infrastructure/sap/mapper.py` (`_entidade`).

### #2 — Código de status "liberado" no `AlteraStatusContrato` (com a SD)
- **Pergunta:** qual valor de `StatusBlock` libera o contrato criado bloqueado (`"06"`)?
- **Contexto:** todo contrato nasce bloqueado. Liberar pela plataforma é a Fase 6; sem o código,
  a liberação continua manual, direto no SAP.
- **Default atual:** nada implementado. O mapper sempre envia `StatusBlock = "06"` na criação.
- **Onde:** Fase 6; ARCHITECTURE §7.

## SD

### #13 — Formato do número do contrato (VBELN)
- **Pergunta:** o número em `SalesContract` sempre tem 10 dígitos com zeros à esquerda? Pode ter
  letras?
- **Contexto:** o número chega pelo SAP (resposta 201) ou é digitado pelo admin na reconciliação de
  `INCERTO`. Os dois precisam gerar o mesmo valor gravado.
- **Default atual:** aceita de 1 a 10 **dígitos ASCII** e grava normalizado para 10 com zeros à
  esquerda (`"40001234"` → `"0040001234"`). Confirmar também com a 1ª resposta 201 real em DEV.
- **Onde:** `backend/app/domain/states.py` (`_numero_sap`).

### #14 — Quais `ConditionType` aceitam `ConditionRateValue` negativo?
- **Pergunta:** quais condições de preço podem ter valor negativo (descontos, abatimentos)?
- **Contexto:** um negativo onde não cabe (ex.: `PR00`) cria contrato com preço errado sem erro
  nenhum.
- **Default atual:** o domínio aceita **qualquer sinal em qualquer condição**. A regra por
  `ConditionType` entraria no caso de uso, por sales org (Fase 3).
- **Onde:** `backend/app/domain/contract.py` (`PRECO`); ARCHITECTURE §8.

### #11 — Valores permitidos de códigos SAP por sales org (com a Sysfértil)
- **Pergunta:** para a BRF1, a lista abaixo está completa e correta? Quais valores cada outra sales
  org aceita?
- **Contexto:** o `$metadata` não enumera esses códigos (são `Edm.String`). O domínio valida só o
  formato e o caso de uso valida o valor, por sales org.
- **Default atual (proposto para BRF1, ainda não implementado, entra na Fase 3):**
  - `PartnerFunction`: `Y1`, `Y2`
  - `ConditionType`: `PR00`, `ZFRE`, `ZCM1`, `ZCM2`
  - `FormPag`: `K`
  - `LongTextID`: `TX01`
  - `SalesContractType`: `ZCON`
  - `Language`: `PT`
- **Onde:** Fase 3 (caso de uso); ARCHITECTURE §8.

### #6 — Diferença entre `ScheduleDate` e `ScheduleDate2`
- **Pergunta:** o que cada uma dessas datas do item significa (ex.: início e fim da entrega)? Alguma
  deve ser posterior à outra?
- **Contexto:** o formulário precisa de um rótulo claro e, se houver ordem, de uma validação.
- **Default atual:** as duas são opcionais, sem nenhuma regra entre elas.
- **Onde:** `backend/app/domain/contract.py` (`ITEM`).

### #12 — Datas base das parcelas precisam ser estritamente crescentes? (com o Comercial)
- **Pergunta:** duas parcelas podem ter a mesma data base? E podem vir fora de ordem?
- **Contexto:** a `Data` da parcela é a data base (ZFBDT), não o vencimento.
- **Default atual:** **estritamente crescentes**. Data igual ou anterior à da parcela anterior dá
  erro `dates_not_increasing`.
- **Onde:** `backend/app/domain/installments.py` (`_validar_datas`).

## Comercial

### #1 — Base de cálculo do valor das parcelas (com a Sysfértil)
- **Pergunta:** o total das parcelas é a soma dos itens (preço × quantidade)? Ou o total com impostos
  e frete? Quem calcula?
- **Contexto:** no exemplo, os itens somam R$ 1.190,67 e as parcelas R$ 23.299,55. Sem regra, as
  parcelas podem não bater com o contrato.
- **Default atual:** as parcelas são calculadas a partir de um `total` **informado explicitamente**
  (`calcular_parcelas`). A comparação com os itens, exibida como aviso, entra no caso de uso da
  Fase 3.
- **Onde:** `backend/app/domain/installments.py`; ARCHITECTURE §8.

### #10 — Campos obrigatórios por sales org (`required_fields`)
- **Pergunta:** para a BRF1, quais campos o processo comercial exige, mesmo que o SAP aceite vazio?
- **Contexto:** o SAP só exige os campos marcados `Mandatory` no `$metadata`. A obrigatoriedade de
  negócio é config da plataforma, por sales org.
- **Default atual (proposto para BRF1, ainda não implementado, entra na Fase 3):**
  - cabeçalho: `SalesOffice`, `SalesGroup`, `SDDocumentReason`, `IncotermsClassification`,
    `IncotermsLocation1`, `CustomerPaymentTerms`, `PurchaseOrderByCustomer`, `PedidoSysFertil`
  - item: `Plant`, `Culture`
- **Onde:** Fase 3 (caso de uso); ARCHITECTURE §8.

### #15 — Existe contrato em outra moeda (ex.: USD)?
- **Pergunta:** algum contrato de venda é fechado em moeda diferente de BRL? Se sim, em quais sales
  orgs e com quais moedas? Cabeçalho, itens e parcelas podem ter moedas diferentes?
- **Contexto:** o `Valor` da parcela tem escala variável conforme a moeda (`Scale=variable` no
  `$metadata`); o mapper hoje fixa 2 casas, o que só é garantido para BRL.
- **Default atual:** política da BRF1 só com **BRL**; sales org sem política é rejeitada; itens e
  parcelas precisam ter a moeda do cabeçalho (item vazio herda).
- **Onde:** `backend/app/domain/politica.py` (`POLITICA_PADRAO`), `backend/app/domain/rules.py`
  (`validar_moedas`).

## Basis

### #8 — Leitura para reconciliar contratos `INCERTO`
- **Pergunta:** o usuário técnico pode ter leitura no serviço standard `API_SALES_CONTRACT_SRV`,
  com filtro por `PurchaseOrderByCustomer`?
- **Contexto:** quando o POST pode ter chegado ao SAP mas a resposta se perdeu, o contrato fica
  `INCERTO`. Com leitura, a plataforma confere sozinha se ele existe.
- **Default atual:** sem leitura. Um admin confere na VA43 e reconcilia manualmente (transições
  `RECONCILIAR_PARA_CRIADO` / `LIBERAR_REENVIO`, com justificativa).
- **Onde:** `backend/app/domain/states.py`; Fase 6 (reconciliação automática).

## Hedge desk (fora dos quatro destinatários principais)

### #7 — `CodTaxa` é o código de uma trava do HedgeSistema?
- **Pergunta:** o `CodTaxa` do contrato corresponde ao código de uma trava do HedgeSistema?
- **Contexto:** se sim, uma integração futura pode preencher e validar o campo automaticamente.
- **Default atual:** campo opcional, até 20 caracteres (`MaxLength` do metadata). Vazio sai `""`.
- **Onde:** `backend/app/domain/contract.py` (`CABECALHO`).
