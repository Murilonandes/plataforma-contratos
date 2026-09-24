# Plataforma de Contratos SAP — Arquitetura

> Status: proposta v1 · Autor: Murilo Fernandes · Data: 2026-09-21

## 1. Objetivo

Criar contratos de venda (`ZCON`) no SAP S/4HANA pelo serviço OData V4 `ZAPI_CONTRATO_VENDAS` (desenvolvido pela Sysfértil) sem que ninguém precise entrar na VA41.

- **Hoje:** o vendedor preenche um formulário.
- **Futuro:** outras origens (n8n, pedido Sysfértil, integrações) chamam a mesma API. Por isso o núcleo é um **serviço de contratos**, e o front é só um dos clientes dele.

## 2. O que "robusto" significa aqui

A robustez tem que estar na **fronteira com o SAP**. Quantidade de serviços não entra nessa conta. Os riscos reais são:

| Risco | Consequência | Como a arquitetura trata |
|---|---|---|
| Timeout no POST | Contrato duplicado no SAP se alguém reenviar | Máquina de estados com estado `INCERTO`. Retry automático **só** quando há garantia de que o request não chegou ao SAP |
| Duplo clique / reenvio da origem | Contrato duplicado | `Idempotency-Key` + unique em `pedido_sysfertil` |
| Erro de arredondamento | Parcelas não fecham 100% / valor total | `Decimal` em tudo, cálculo de parcelas no servidor pelo método do maior resto |
| Payload inválido chega ao SAP | Erro críptico para o vendedor | Validação de domínio espelhando o `$metadata` antes de enfileirar |
| Ninguém sabe o que foi enviado | Sem auditoria | Snapshot imutável do payload enviado, da resposta e das `sap-messages` |
| Credencial vazada | Acesso ao SAP | Docker secrets, usuário técnico com role mínima, nunca no código ou no log |

**Não entra, de propósito:** microserviços, Kafka, Redis, Kubernetes. É um dev e um volume baixo (dezenas a centenas de contratos por dia). Um **monólito modular** com fila em Postgres entrega a mesma garantia com metade da operação.

## 3. Visão geral

```
                 ┌──────────────┐
 Vendedor ──────▶│  Web (React) │──┐
  (Entra ID SSO) └──────────────┘  │  HTTPS + JWT (Entra ID)
                                   ▼
 n8n / futuras ─────────────▶┌──────────────────────┐
 origens (client creds)      │   API (FastAPI)      │
                             │  - valida (domínio)  │
                             │  - calcula parcelas  │
                             │  - grava + enfileira │  (mesma transação)
                             └─────────┬────────────┘
                                       │
                             ┌─────────▼────────────┐
                             │  PostgreSQL 16       │
                             │  contracts           │
                             │  contract_events     │  (audit, append-only)
                             │  outbox_jobs         │  (fila: SKIP LOCKED)
                             └─────────▲────────────┘
                                       │ poll
                             ┌─────────┴────────────┐       ┌───────────────────┐
                             │  Worker (mesma img)  │──────▶│ SAP S/4 OData V4  │
                             │  - CSRF + POST       │ HTTPS │ CriaContrato      │
                             │  - classifica result │◀──────│ AlteraStatus...   │
                             └──────────────────────┘       └───────────────────┘
```

**Por que o envio é assíncrono (API → fila → worker):**
- O SAP pode demorar. O request HTTP do vendedor não fica pendurado nem sujeito ao timeout do Traefik.
- Se o worker cair no meio, o job continua na fila, com estado conhecido.
- A origem futura (n8n) recebe `202 Accepted` + `id` e consulta o status. O mesmo contrato serve para todas as origens.
- O front faz polling em `GET /contracts/{id}` (ou SSE depois). O resultado costuma aparecer em poucos segundos.

## 4. Máquina de estados

Toda transição gera uma linha em `contract_events` (quem, quando, de/para, detalhe, justificativa se aplicável) e exige `ator` (`user` uuid Entra ID, `admin` uuid Entra ID, `worker` id do processo, ou `system` — usado só na recuperação de lock expirado). Transições com "sim" em `justif.` exigem justificativa em texto livre.

Estados terminais: `CRIADO`, `CANCELADO`. Editáveis pelo vendedor: `RASCUNHO` e `ERRO_NEGOCIO`. `ERRO_TECNICO` e `INCERTO` só admin decide o próximo passo. `RASCUNHO → CANCELAR` não pede justificativa (é rascunho).

**Matriz de transições:**

| De             | Evento                     | Para          | Ator     | Justif. | Observação                                                        |
|---             |---                         |---            |---       |---      |---                                                                |
| RASCUNHO       | SUBMETER                   | NA_FILA       | user     | não     | Cria job no outbox                                                |
| RASCUNHO       | CANCELAR                   | CANCELADO     | user     | não     | Rascunho, sem justificativa                                       |
| NA_FILA        | WORKER_PEGOU               | ENVIANDO      | worker   | não     | Job aberto; body do POST ainda **não** foi enviado                |
| NA_FILA        | CANCELAR                   | CANCELADO     | admin    | sim     |                                                                   |
| ENVIANDO       | SAP_201                    | CRIADO        | worker   | não     | Guarda `sap_contract_number`                                      |
| ENVIANDO       | SAP_4XX_NEGOCIO            | ERRO_NEGOCIO  | worker   | não     | Ver precedência na classificação abaixo                           |
| ENVIANDO       | SAP_4XX_TECNICO            | ERRO_TECNICO  | worker   | não     | Ver precedência na classificação abaixo. Sem retry. Alerta        |
| ENVIANDO       | FALHA_ANTES_POST           | NA_FILA       | worker   | não     | `attempts < N`. Body **não** enviado. Retry permitido             |
| ENVIANDO       | FALHA_ANTES_POST_ESGOTOU   | ERRO_TECNICO  | worker   | não     | `attempts ≥ N`. Estourou o teto                                   |
| ENVIANDO       | TIMEOUT_APOS_POST          | INCERTO       | worker   | não     | Body enviado, read timeout. **Sem retry**                         |
| ENVIANDO       | CONEXAO_CAIDA_APOS_POST    | INCERTO       | worker   | não     | Body enviado, conexão TCP quebrou (ou write parcial). **Sem retry** |
| ENVIANDO       | SAP_5XX_APOS_POST          | INCERTO       | worker   | não     | Body enviado, 5xx do SAP. LUW não é garantia. **Sem retry**       |
| ENVIANDO       | LOCK_EXPIRADO_SEM_ENVIO    | NA_FILA       | system   | não     | Worker morreu; **não** há linha em `contract_submissions`         |
| ENVIANDO       | LOCK_EXPIRADO_COM_ENVIO    | INCERTO       | system   | não     | Worker morreu; **há** linha em `contract_submissions`             |
| ERRO_NEGOCIO   | SUBMETER                   | NA_FILA       | user     | não     | Após correção do vendedor                                         |
| ERRO_NEGOCIO   | CANCELAR                   | CANCELADO     | user     | sim     |                                                                   |
| ERRO_TECNICO   | LIBERAR_REENVIO            | NA_FILA       | admin    | sim     |                                                                   |
| ERRO_TECNICO   | CANCELAR                   | CANCELADO     | admin    | sim     |                                                                   |
| INCERTO        | RECONCILIAR_PARA_CRIADO    | CRIADO        | admin    | sim     | Conferiu VA43; grava `sap_contract_number` + justificativa        |
| INCERTO        | LIBERAR_REENVIO            | NA_FILA       | admin    | sim     | Conferiu VA43 e não achou; libera reenvio                         |
| INCERTO        | CANCELAR                   | CANCELADO     | admin    | sim     |                                                                   |

**Total: 21 transições válidas.** Qualquer par `(estado, evento)` fora da matriz é `InvalidTransitionError`, antes de olhar qualquer dado. A tabela acima é a fonte única: `test_states` lê as 21 linhas e confere a matriz do código campo a campo.

**Dados por transição** (`domain/states.py`, validados de forma acumulada numa única `DomainValidationError`):

- **Ator:** `kind` igual à coluna Ator; `identifier` não vazio.
- **Justificativa:** com "sim" em `Justif.` é obrigatória. Com "não", é opcional para `user`/`admin` (se vier, é validada e gravada) e **proibida** para `worker`/`system`. Quando vem, tem de 10 a 500 caracteres depois do strip (`"."` e `"ok"` não valem).
- **`detalhe`:** só `worker`/`system`, para dado técnico estruturado (chave snake_case de até 40 caracteres, valor `int` ou `str` de até 200). Nunca texto livre em `justificativa`.
- **`sap_contract_number`:** obrigatório em `SAP_201` e `RECONCILIAR_PARA_CRIADO` (até 10, só dígitos: VBELN, `TODO(decisão #13)`); proibido nas demais transições.
- String vazia ou só com espaços conta como ausente.
- O resultado (`Transicao`: `de`, `para`, `evento`, `ator`, `justificativa`, `sap_contract_number`, `detalhe`) é o registro do evento em `contract_events`. O domínio não tem timestamp: `occurred_at` é carimbado pelo caso de uso via porta `Clock`.

### Classificação de falhas em runtime

Enquanto o worker está vivo, o evento é escolhido pelo **tipo da exceção `httpx`** (ou pelo status HTTP se houver resposta). Nenhuma dessas classes cai em lock recovery — o worker escolhe a transição direto no `except`.

**Exceções de transporte:**

| Situação                          | Exceção `httpx`                                                     | Evento                     |
|---                                |---                                                                  |---                         |
| CSRF fetch falhou                 | qualquer erro no `GET` prévio para pegar o token                    | `FALHA_ANTES_POST`         |
| conexão não estabeleceu           | `httpx.ConnectError`, `httpx.ConnectTimeout`                        | `FALHA_ANTES_POST`         |
| envio do body falhou              | `httpx.WriteError`, `httpx.WriteTimeout`                            | `CONEXAO_CAIDA_APOS_POST` (conservador — write parcial pode ter chegado) |
| resposta não veio (timeout)       | `httpx.ReadTimeout`                                                 | `TIMEOUT_APOS_POST`        |
| resposta não veio (conexão)       | `httpx.ReadError`, `httpx.RemoteProtocolError`                      | `CONEXAO_CAIDA_APOS_POST`  |

**Respostas HTTP — precedência de 4xx (primeiro match vence):**

1. `5xx` → `SAP_5XX_APOS_POST`.
2. `403` com header `x-csrf-token: Required` → **não é transição de estado**; refetch do token + reenvio do POST **uma vez** dentro da mesma tentativa (ver caso especial abaixo).
3. Status ∈ `{401, 403, 404, 405, 415}` → `SAP_4XX_TECNICO` — **precedência sobre negócio**, mesmo se a resposta vier com `error.details`. Sem retry, gera alerta.
4. Status ∈ `{400, 409, 422}` → `SAP_4XX_NEGOCIO`.
5. Qualquer outro `4xx` **com** `error.details` do RAP → `SAP_4XX_NEGOCIO`.
6. Qualquer outro `4xx` **sem** `error.details` → `SAP_4XX_TECNICO` (default conservador; admin avalia).

**Caso especial CSRF 403.** Se a resposta for `403` com header `x-csrf-token: Required`, o SAP rejeitou **sem processar** — refetch do token + reenvio do POST **uma vez** dentro da mesma tentativa. Se o segundo POST falhar por qualquer razão, aí sim classifica pela precedência acima. Se falhar por CSRF de novo, `SAP_4XX_TECNICO`.

### `request_sent_at` e recuperação de lock expirado

A linha em `contract_submissions` (com `request_sent_at = NOW()`, `request_body`, e demais campos preenchíveis pré-POST) é **inserida e commitada antes** de chamar `httpx.post(...)`. É o marcador durável para o caso em que o worker **morre silenciosamente** (não levanta exceção — o processo simplesmente encerra). Quando outro worker chega para recuperar o job cujo `locked_until` expirou:

- **Sem** linha em `contract_submissions` para essa tentativa → `LOCK_EXPIRADO_SEM_ENVIO` → `NA_FILA` (seguro: o POST não saiu).
- **Com** linha em `contract_submissions` (independente de haver `response_status`) → `LOCK_EXPIRADO_COM_ENVIO` → `INCERTO` (conservador: pode ter chegado no SAP).

O marcador vale **exclusivamente para recovery**. Em runtime a classificação é pela exceção (tabelas acima), não pelo marcador — inclusive `ConnectError` levantado **depois** do commit da submissão continua sendo `FALHA_ANTES_POST`.

Futuro: se liberarem leitura (serviço standard `API_SALES_CONTRACT_SRV` filtrando por `PurchaseOrderByCustomer`), a reconciliação de `INCERTO` vira automática.

## 5. Estrutura do código (hexagonal leve)

```
backend/
  app/
    domain/            # PURO: sem FastAPI, sem SQL, sem HTTP
      contract.py      #   entidades/VOs (Contract, Item, Partner, Installment...)
      rules.py         #   validações do $metadata + regras de negócio
      installments.py  #   cálculo de parcelas (maior resto, Decimal)
      states.py        #   máquina de estados + transições permitidas
    application/       # casos de uso (orquestra domínio + portas)
      submit_contract.py
      process_outbox_job.py
      reconcile_uncertain.py
      ports.py         #   Protocols: ContractRepo, SapContractGateway, Clock...
    infrastructure/
      db/              #   SQLAlchemy 2 async, Alembic, repos
      sap/             #   httpx client, CSRF, mapper domínio→payload OData, parser de erros
      auth/            #   validação JWT Entra ID, roles
      observability/   #   structlog, métricas Prometheus, correlation id
    api/               #   routers FastAPI, schemas Pydantic de entrada/saída
    worker/            #   loop do outbox (entrypoint separado, mesma imagem)
    settings.py        #   pydantic-settings, por ambiente
  migrations/
  tests/
    unit/ integration/ contract/   # contract = fixtures reais do SAP
frontend/
  src/
    api/               #   client gerado do OpenAPI (openapi-typescript + openapi-fetch)
    features/contracts/
    auth/              #   MSAL React
infra/
  docker/ (Dockerfile multi-stage backend e frontend)
  stack.dev.yml stack.prod.yml     # Docker Swarm
.github/workflows/ci.yml
docs/
```

A regra de dependência é `domain` ← `application` ← `infrastructure`/`api`. Um teste de arquitetura (`import-linter`) quebra o CI se alguém importar SQLAlchemy ou httpx dentro de `domain/`.

## 6. Modelo de dados (essencial)

- **`contracts`**: `id` (uuid), `status`, `origin` (`WEB`/`API`), `created_by` (oid Entra), `sales_org`, `sold_to`, `pedido_sysfertil`, `purchase_order_by_customer`, `payload` (JSONB, forma canônica do domínio), `sap_contract_number` (null até criar), `idempotency_key`, `version` (lock otimista), timestamps.
  - `UNIQUE (idempotency_key)`
  - `UNIQUE (pedido_sysfertil) WHERE pedido_sysfertil IS NOT NULL AND status NOT IN ('ERRO_NEGOCIO','CANCELADO')`. Barra a duplicidade de negócio mesmo com chaves de idempotência diferentes. **`pedido_sysfertil` vazio é gravado como `NULL`** no banco (não `""`) — o mapper serializa `""` no payload OData (regra `Edm.String`), mas o repositório converte para `NULL` na persistência, para não colidir com o índice quando várias origens deixam o campo em branco.
- **`contract_submissions`**: uma linha por tentativa de POST. **Inserida e commitada antes do POST** com `request_body` exato (JSONB) e `request_sent_at` (`TIMESTAMPTZ`); atualizada após a resposta com `response_status`, `response_body`, `sap_messages`, `duration_ms`, `error_class`. `error_class` mapeia a exceção `httpx` ou o status SAP (ver classificação na §4). Imutável após o `UPDATE` final. A **ausência** dessa linha na recuperação de lock indica que o body não foi enviado (`LOCK_EXPIRADO_SEM_ENVIO`); a **presença** indica que pode ter sido (`LOCK_EXPIRADO_COM_ENVIO`).
- **`contract_events`**: audit trail append-only das transições de estado.
- **`outbox_jobs`**: `id`, `contract_id`, `kind` (`CREATE`, depois `CHANGE_STATUS`), `run_after`, `attempts`, `locked_until`, `last_error`. O worker usa `SELECT … FOR UPDATE SKIP LOCKED`.

Dinheiro e quantidade são `NUMERIC` no banco e `Decimal` no Python. **Proibido float.**

## 7. Integração SAP (detalhes que importam)

Fonte da verdade: `docs/sap/metadata.xml`. Payload de referência: `docs/sap/payload_exemplo.json`.

- **Endpoint:** `POST {base}/CriaContrato?sap-client={client}` com deep insert (itens, parceiros, preços, parcelas e textos aninhados). Os filhos não aceitam insert direto.
- **Auth:** usuário técnico com Basic Auth (Docker secret) e `saml2=disabled`.
- **CSRF:** `GET {base}` com `x-csrf-token: Fetch` usando a mesma sessão/cookies. Cachear o token. Em `403` com header `x-csrf-token: Required`, refaz o fetch **uma vez**. Isso conta como falha antes do POST, então pode repetir.
- **Decimais:** enviar com `Content-Type: application/json;IEEE754Compatible=true` e decimais como **string**. Isso evita float na serialização. ⚠️ Validar no primeiro teste em DEV. Deixar como flag de config (`SAP_DECIMAL_AS_STRING`).
- **Datas:** `Edm.Date` → `YYYY-MM-DD`.
- **Strings `Nullable=false`:** vazio vai como `""`, nunca `null`. O mapper garante isso.
- **Campos `Computed`** (`SalesContract`, `SalesContractItem`, `ConditionUUID`) nunca são enviados.
- **Resposta 201:** o número vem em `SalesContract`. Guardar também o header `sap-messages` (warnings).
- **Erros:** `error.code`, `error.message`, `error.details[]` (`code`, `message`, `target`). O parser converte isso em uma lista de mensagens para o usuário e, quando o `target` permitir, mapeia para o campo do formulário.
- **Timeouts:** connect 5s, read 90s (configurável).
- **Leitura:** todos os entity sets têm `Readable=false`. Não existe GET de contrato neste serviço.
- **`AlteraStatusContrato`:** `PATCH` para liberar bloqueio. Fora do MVP (depende do código "liberado").

## 8. Regras de domínio (vindas do $metadata + negócio)

**Obrigatoriedade — duas camadas independentes:**

- **API-mandatory** (validado sempre nos VOs do domínio): **exclusivamente** os campos com anotação `SAP__common.FieldControl EnumMember="com.sap.vocabularies.Common.v1.FieldControlType/Mandatory"` no `$metadata`. A anotação `Nullable="false"` **não** implica obrigatoriedade — todas as strings do serviço são `Nullable="false"` (incluindo `SalesOffice`, `CodTaxa`, `Supplier`...), o que só significa "envie `""` em vez de `null`". A lista canônica sai do XML: um teste extrai automaticamente as propriedades com a anotação `FieldControl/Mandatory` e o domínio marca esses campos como `required=True`. **Critério de sanidade:** `docs/sap/payload_exemplo.json` **precisa passar** sem `ValidationError` nos VOs construídos a partir dele — se um campo entra como API-mandatory que o exemplo não preenche, ou o exemplo está errado, ou a anotação foi lida errado.
- **Negócio-mandatory** (validado no caso de uso, **não** no VO): configuração `required_fields` por organização de vendas. O SAP aceita `""`, mas o processo comercial exige o campo — o caso de uso rejeita antes de enfileirar. **Default proposto para BRF1** (`TODO(decisão #10)` — validar com o comercial): cabeçalho: `SalesOffice`, `SalesGroup`, `SDDocumentReason`, `IncotermsClassification`, `IncotermsLocation1`, `CustomerPaymentTerms`, `PurchaseOrderByCustomer`, `PedidoSysFertil`; item: `Plant`, `Culture`. Nesses, o VO aceita `""` sem reclamar; quem barra é o caso de uso quando a sales org listar o campo em `required_fields`.

Não existe "FieldControl por organização de vendas" no `$metadata` — a variação por sales org vive na config `required_fields` da plataforma, separada da obrigatoriedade fixa da API.

**Códigos do SAP não são enum.** O `$metadata` não enumera `PartnerFunction`, `ConditionType`, `FormPag`, `LongTextID`, `Language`, `SalesContractType` etc.: são `Edm.String` com `MaxLength` (ex.: `PartnerFunction` 2, `ConditionType` 4, `FormPag` 1, `LongTextID` 4, `Language` 2, `SalesContractType` 4). Duas camadas, como na obrigatoriedade:

- **Domínio (VO):** só formato — `MaxLength` do metadata, uppercase, não vazio quando o campo for API-mandatory. Nenhuma lista de valores no código.
- **Caso de uso:** valores permitidos vêm de **config por sales org** (mesma estrutura do `required_fields`), validados antes de enfileirar. `enums.py` do domínio só tem conceitos nossos: `ContractStatus`, `TransitionEvent`, `ActorKind`.

Default proposto para **BRF1** (`TODO(decisão #11)` — confirmar com SD/Sysfértil):

| Campo | Valores permitidos | Origem |
|---|---|---|
| `PartnerFunction` | `Y1`, `Y2` | `payload_exemplo.json` |
| `ConditionType` | `PR00`, `ZFRE`, `ZCM1`, `ZCM2` | `payload_exemplo.json` |
| `FormPag` | `K` | `payload_exemplo.json` |
| `LongTextID` | `TX01` | `payload_exemplo.json` |
| `SalesContractType` | `ZCON` | `payload_exemplo.json` (acrescentado; confirmar) |
| `Language` | `PT` | `payload_exemplo.json` (acrescentado; confirmar) |

**Formato:**
- **MaxLength:** conforme o metadata. Destaque: `SalesContractItemText` tem no máximo 40.
- **`RequestedQuantity`:** 3 casas decimais.
- **`ConditionRateValue`:** até 9 casas. Normalizar para 2 casas quando for BRL (confirmar).
- **Parceiros:** no máximo um por `PartnerFunction`.
- **Datas** são `Edm.Date` (`YYYY-MM-DD`). Data **ausente** = **chave omitida** do payload; **nunca** vira `""` (isso é comportamento só de `Edm.String`). O mapper garante a omissão. `TODO(decisão #9)` — confirmar comportamento no primeiro POST em DEV; se o SAP rejeitar omissão, avaliar `null` explícito.

**Parcelas:**
- **`to_FormPag` NUNCA vem do cliente.** A entrada da API/caso de uso é `total` + `pesos` + `datas` + `FormPag`; as parcelas são sempre montadas por `calcular_parcelas` (`domain/installments.py`). O schema de entrada (Fase 3) não tem `to_FormPag`.
- São calculadas pelo servidor a partir de N parcelas e datas base.
- **Entrada:** `total` segue a especificação de `Valor` (Decimal > 0, 2 casas, até 13 dígitos inteiros); `pesos` tem de 1 a **36** inteiros, cada um entre 1 e **10.000** (comporta pontos-base, ex. 3333 = 33,33%); `datas` do mesmo tamanho, **estritamente crescentes** (`TODO(decisão #12)`).
- **Mínimo por parcela, pela cota exata:** cada parcela precisa de cota exata ≥ `0.01`, ou seja `total ≥ 0.01 × soma(pesos) / menor peso`. A regra é monotônica, então o erro `installment_below_minimum` informa o `minimo_total` exato e as duas saídas (aumentar o total ou equilibrar os pesos). Com o teto de peso e N ≤ 36, a menor `Porcentagem` fica ≥ ~`0.0003`, nunca abaixo de `0.0001`.
- `Porcentagem` tem 4 casas e soma exatamente `100.0000`. `Valor` tem 2 casas e soma exatamente o total. O **desempate (resíduo)** segue o método do **maior resto** (Hare / largest remainder), aplicado **de forma independente** para `%` e para valor:
  1. Base = divisão exata `.quantize(unidade, ROUND_DOWN)` por parcela.
  2. Resíduo = quantas unidades faltam para bater a soma-alvo (`100.0000` em `%`, `total` em `BRL`).
  3. Ordena os índices pela **parte fracionária** da divisão exata em ordem **decrescente**; empate → menor índice primeiro (chave `(-resto, índice)`, em aritmética inteira).
  4. Adiciona uma unidade (`0.0001` em `%`, `0.01` em `BRL`) para os `residuo` primeiros dessa ordem.
  
  Quando todas as parcelas têm o mesmo peso, todos os fracionários empatam e o resíduo cai nas parcelas de **menor índice** — daí o caso comum "primeiras parcelas". Para pesos desiguais (ex.: `[30, 70]`, `[1, 1, 1, 97]`), o algoritmo geral escolhe corretamente.
- `Data` da parcela = **data base** (ZFBDT), não vencimento. A UI tem que rotular assim.

**Defaults:**
- `StatusBlock = "06"` na criação (bloqueado). "Servidor" aqui = **o nosso backend**: o mapper sempre grava `"06"` no payload enviado ao SAP. O domínio não aceita `StatusBlock` como input (o campo não existe nos VOs). Omitir esse campo criaria contrato **desbloqueado** no SAP — teste explícito no mapper garante presença em qualquer input.
- **`CodTaxa`** é opcional (integração futura com HedgeSistema, decisão #7). O domínio aceita `""` e o mapper serializa `""`. Nunca `null`.

**⚠️ Pendência:** qual é a base do valor das parcelas? No exemplo, os itens somam R$ 1.190,67 e as parcelas R$ 23.299,55. Enquanto não houver definição, o domínio calcula a partir de um `total` informado explicitamente e mostra a divergência em relação aos itens como aviso.

Configuração por organização de vendas (tipo de contrato, canal, setor, escritórios) fica em tabela ou config, e não hardcoded no front.

## 9. Segurança

- **Entra ID:** SPA com MSAL e PKCE. A API valida o JWT (issuer, audience, assinatura via JWKS).
- **App roles:**
  - `Contratos.Vendedor`: cria e vê os próprios contratos.
  - `Contratos.Admin`: vê tudo e reconcilia `INCERTO`.
  - `Contratos.Integracao`: client credentials, para o n8n.
- **Autorização** checada no caso de uso, não só na rota.
- **Credenciais SAP** via Docker secret, lidas por arquivo. Nunca em env de compose versionado nem em log. O `Authorization` é redigido nos logs e no `request_body` salvo.
- **Rede:** o CORS só permite o domínio do front. Rate limit por usuário no `POST /contracts`.
- **CI:** `pip-audit`, `npm audit`, `trivy` na imagem e `gitleaks`.

## 10. Observabilidade

- **Logs:** `structlog` em JSON com `correlation_id` (header `X-Request-ID`, propagado para o worker via job), `contract_id` e `sap_contract_number`.
- **Métricas Prometheus** em `/metrics`:
  - `contracts_submitted_total{origin}`
  - `contracts_result_total{status}`
  - `sap_request_duration_seconds`
  - `outbox_queue_depth`
  - `outbox_oldest_job_age_seconds`
- **Health:** `/health/live` e `/health/ready` (banco ok + último fetch de CSRF ok). O Zabbix e o Grafana existentes consomem esses endpoints.
- **Alertas:**
  - qualquer contrato em `INCERTO`
  - fila com job mais velho que 5 min
  - taxa de `ERRO_TECNICO` acima do limite

## 11. Testes

| Camada | Ferramenta | O que cobre |
|---|---|---|
| Domínio | pytest + **hypothesis** | Parcelas sempre fecham 100% e o total, para qualquer N ou valor. Validações do metadata |
| SAP adapter | pytest + **respx** | CSRF, refresh em 403, classificação de erro/timeout (antes vs. depois do POST), parser de `error.details` |
| Contract tests | fixtures JSON reais de DEV | Resposta 201 e erros reais gravados, anonimizados |
| Integração | **testcontainers** Postgres | Outbox com `SKIP LOCKED` e concorrência de 2 workers, constraints de unicidade, idempotência |
| API | httpx AsyncClient | Auth/roles, 202 + polling, 409 em duplicidade |
| Front | Vitest + Testing Library | Form, validações Zod, cálculo de parcelas exibido |
| E2E | **Playwright** | Fluxo vendedor contra SAP mockado |
| Smoke DEV | script manual | `docs/sap/smoke_test_referencia.py` contra `s4-dev` |

Cobertura mínima de 90% em `domain/` e `application/`. Nas outras camadas não tem meta numérica.

## 12. CI/CD e ambientes

- **Git:** `develop` → DEV, `main` → PRD (mesmo modelo do HedgeSistema).
- **GitHub Actions:**
  - backend: `ruff` → `mypy --strict` → `import-linter` → `pytest`
  - frontend: `eslint` → `tsc` → `vitest` → `playwright`
  - segurança: `pip-audit` / `npm audit` / `gitleaks`
  - imagens: build + `trivy`, depois push
- **Deploy:** Docker Swarm atrás do Traefik. `api`, `worker` e `web` são serviços; `worker` com 1 réplica para começar (`SKIP LOCKED` permite escalar).
- **Ambientes SAP:**
  - DEV → `s4-dev` client 300
  - QAS → a definir
  - PRD → a definir
  - Um guard impede que a instância DEV da plataforma aponte para o SAP PRD (checa host × `APP_ENV` no startup).
- **Migrações:** Alembic rodando como job antes do rollout.

## 13. Fases

0. **Fundação:** monorepo, Docker, CI verde, settings, logging, health.
1. **Domínio:** modelos, validações, parcelas, estados. 100% testado, sem I/O.
2. **Adapter SAP + worker + outbox:** mapper, client CSRF, classificação de falhas. Smoke real em DEV.
3. **API + auth:** endpoints, roles, idempotência, OpenAPI.
4. **Front:** form de contrato (cabeçalho, itens, parceiros, parcelas calculadas, textos), lista/histórico, detalhe com erros SAP e timeline de eventos.
5. **Hardening:** métricas/alertas, E2E, tela de reconciliação `INCERTO` (admin), deploy Swarm.
6. **Pós-MVP:** liberar bloqueio via `AlteraStatusContrato`, value helps (cliente/material/centro via APIs standard), origem n8n, reconciliação automática.

## 14. Decisões em aberto

| # | Pergunta | Quem responde | Impacto |
|---|---|---|---|
| 1 | Base de cálculo do valor das parcelas (itens × total com impostos?) | Comercial/Sysfértil | Regra de parcelas |
| 2 | Código de status "liberado" para o `AlteraStatusContrato` | Sysfértil/SD | Fase 6 |
| 3 | Formato exato da resposta 201 e das mensagens | 1º teste DEV | Mapper/parser |
| 4 | Decimal como número ou string (`IEEE754Compatible`) | 1º teste DEV | Flag |
| 5 | MaxLength real de `NotaInternaCli`, `PedidoSysFertil`, `Culture`, `LongText` | Sysfértil | Validação |
| 6 | Diferença entre `ScheduleDate` e `ScheduleDate2` | SD | Label no form |
| 7 | `CodTaxa` é o código de uma trava do HedgeSistema? | Hedge desk | Integração futura |
| 8 | Leitura para reconciliação (`API_SALES_CONTRACT_SRV` liberado ao usuário técnico?) | Basis | Automatizar `INCERTO` |
| 9 | Comportamento de `Edm.Date` ausente no payload (omitir chave vs `null` explícito) | 1º teste DEV | Mapper |
| 10 | Lista `required_fields` por sales org (default BRF1: `SalesOffice`, `SalesGroup`, `SDDocumentReason`, `IncotermsClassification`, `IncotermsLocation1`, `CustomerPaymentTerms`, `PurchaseOrderByCustomer`, `PedidoSysFertil`; item: `Plant`, `Culture`) | Comercial | Negócio-mandatory |
| 11 | Valores permitidos de códigos SAP por sales org (default BRF1: `PartnerFunction` `Y1`/`Y2`; `ConditionType` `PR00`/`ZFRE`/`ZCM1`/`ZCM2`; `FormPag` `K`; `LongTextID` `TX01`; `SalesContractType` `ZCON`; `Language` `PT`) | SD/Sysfértil | Validação no caso de uso (§8) |
| 12 | Datas base das parcelas precisam ser estritamente crescentes? (hoje o domínio rejeita data igual ou anterior à da parcela anterior) | SD/Comercial | Validação de parcelas (§8) |
| 13 | Formato do número do contrato SAP (`SalesContract`/VBELN): sempre 10 dígitos com zeros à esquerda? Só dígitos? | SD | Validação de `SAP_201`/`RECONCILIAR_PARA_CRIADO` (§4) |

## 15. Revisitar quando crescer

- **Volume maior que ~10k/dia ou muitas origens:** trocar a fila Postgres por um broker.
- **Mais de um sistema consumindo eventos:** publicar `contract.created` (webhook/n8n).
- **Value helps lentos:** cache de mestres (clientes/materiais) com refresh agendado.
