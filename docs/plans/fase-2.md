# Plano — Fase 2 (Adapter SAP + worker + outbox)

> Status: **revisão 5, aprovada** (com os ajustes do dono do projeto em D3, D4, D5, D7 e D10 e nas
> tarefas 2.1, 2.3, 2.5, 2.7, 2.10 e 2.12). Execução a partir da 2.1; a 2.0 espera o `metadata.xml`.
> Fonte da verdade do design: `docs/ARCHITECTURE.md` (§4 máquina de estados e classificação de
> falhas, §6 modelo de dados, §7 integração SAP, §10 observabilidade). Regras invioláveis: `CLAUDE.md`.

## Objetivo

Um contrato válido, gravado com status `NA_FILA` e um job no outbox, é enviado ao SAP DEV pelo
worker, e o resultado vira a transição certa da §4. Isso vale para os casos abaixo:
- **sucesso:** `CRIADO`, com o número do contrato;
- **erro de negócio ou técnico:** `ERRO_NEGOCIO` ou `ERRO_TECNICO`, com as mensagens do SAP;
- **falha antes do POST:** volta a `NA_FILA` para retry;
- **qualquer dúvida sobre se o POST chegou ao SAP:** `INCERTO`, **sem retry**.

Tudo auditado em `contract_events` e `contract_submissions`, e comprovado num smoke real em DEV.

## Fora do escopo (fases seguintes)

- Endpoints HTTP, auth Entra ID e idempotência na API (Fase 3). Nesta fase, contratos entram por
  caso de uso chamado de teste ou script, sem rota.
- Carga da política por sales org de settings/banco (Fase 3). Continua `POLITICA_PADRAO`.
- Métricas Prometheus e alertas (Fase 5). Aqui só logs e o readiness.
- `AlteraStatusContrato` e reconciliação automática de `INCERTO` (Fase 6).

## Bloqueios (do lado do dono do projeto)

1. **Docker local**, para os testes de integração com Postgres (testcontainers). O CI já tem Docker;
   sem Docker local, esses testes só rodam no CI.
2. **`docs/sap/metadata.xml` corrigido**, com o diff esperado de só `&` → `&amp;`. Com ele entra a
   Tarefa 2.0: commit, remoção do workaround de escape em `_referencias_sap.py` e teste de XML bem
   formado.
3. **Credenciais do usuário técnico SAP DEV** num Docker secret, e autorização explícita **a cada
   execução** do smoke, que cria contrato de verdade no SAP DEV.

## Decisões de design para aprovar

| # | Decisão | Proposta |
|---|---|---|
| D1 ✅ | Onde fica o caso de uso que coloca o contrato na fila (`submit_contract`: `RASCUNHO`/`ERRO_NEGOCIO` → `NA_FILA` + job, na mesma transação) | **Nesta fase**, na camada de aplicação e sem rota: é o produtor do outbox e o smoke precisa dele. A rota HTTP vem na Fase 3. |
| D2 ✅ | Fronteiras de transação do worker | Três commits por tentativa: (1) pega o job com `FOR UPDATE SKIP LOCKED`, grava `locked_until` e faz `WORKER_PEGOU` (→ `ENVIANDO`); (2) grava a linha em `contract_submissions` (`request_sent_at`, `request_body`); (3) depois da resposta, grava o resultado, a transição, o evento e o estado do job. O fetch de CSRF acontece **entre 1 e 2**, então falha nele não deixa marcador e é `FALHA_ANTES_POST`. |
| D3 ✅ | Retry antes do POST | `SAP_MAX_TENTATIVAS` (default 5) com backoff exponencial em `run_after` (30 s × 2^n, teto de 30 min) e **jitter de ±20%**. Ao atingir o teto: `FALHA_ANTES_POST_ESGOTOU`. Retry **só** para erro de rede (`Connect*`) e `5xx` no CSRF. **`401`/`403` no fetch do CSRF não repete:** vai direto para `ERRO_TECNICO` (`SAP_4XX_TECNICO`, `detalhe` `fase=csrf`) com alerta, porque repetir login pode bloquear o usuário técnico no SAP. A linha nova está na tabela de classificação da §4 e tem teste. |
| D4 ✅ | Tempo de lock e fencing | **Prazo total por tentativa** (`asyncio.timeout`) = soma do pior caso: CSRF + POST + refetch do CSRF + POST, cada um com connect + read, ou seja 4 × (5 + 90) = 380 s com os defaults. `SAP_LOCK_TIMEOUT_S` (default 600) precisa ser **maior que esse prazo + 60 s**, e o guard do settings faz a conta. **Fencing:** o job ganha `lock_token` (uuid novo a cada pega). O commit do resultado (commit 3) é `UPDATE … WHERE lock_token = :meu`; se o lock foi recuperado por outro worker, não grava nada e loga `WARNING`, e quem decide é o recover (`LOCK_EXPIRADO_COM_ENVIO` → `INCERTO`). Há teste de integração desse caso. |
| D5 ✅ | Relógio | Porta `Clock` na aplicação: `occurred_at`, `request_sent_at`, `run_after` e `locked_until` vêm dela, nunca de `datetime.now()` espalhado. Toda comparação de tempo nas queries (`locked_until`, `run_after`) usa o horário do `Clock` **passado como parâmetro**, nunca `now()` do banco misturado com o relógio da aplicação. Os testes usam um relógio fixo. |
| D6′ | Snapshot na submissão (substitui a D6 rejeitada) | Na submissão, o contrato é **congelado completo**: o `Contract` do domínio **já com as parcelas calculadas**, serializado em forma canônica (decimal como string de escala fixa), mais a versão do algoritmo de parcelas (`ALGORITMO_PARCELAS`, ex.: `maior-resto/1`) e a entrada que as gerou (`total`, `pesos`, `datas`, `FormPag`). O snapshot fica em tabela **append-only** `contract_snapshots`, uma linha por submissão; o job e cada `contract_submissions` apontam para o `snapshot_id`. O worker envia **exatamente** esse snapshot: `Contract.criar(snapshot)` → mapper → `to_json`. Recalcular as parcelas serve **só como conferência**; se divergir, o POST não é feito, e o contrato vai para `ERRO_TECNICO` com alerta (ver D11). `LIBERAR_REENVIO` reusa o **mesmo** `snapshot_id` e nunca recalcula. Nova submissão depois de `ERRO_NEGOCIO` (vendedor corrigiu) gera snapshot novo. |
| D7 ✅ | O que o worker grava em `request_body` | `request_body` **`BYTEA`** com os bytes exatos enviados, mais `request_sha256`. `request_json` `JSONB` opcional, só como cópia para consulta. Headers **não** são gravados, então o `Authorization` nunca chega ao banco. O teste de reenvio compara os bytes **e** o hash. |
| D8 ✅ | Classificação de resultado | Módulo **puro** `infrastructure/sap/classificacao.py`: exceção do httpx ou (status, headers, corpo) → `TransitionEvent` + detalhe estruturado, seguindo as duas tabelas da §4. Teste exaustivo lê as tabelas do `.md`, como o `test_states`. **Entra no gate de mutação**, porque decide quando não reenviar. |
| D9 ✅ | Número do contrato na resposta 201 | Lido de `SalesContract` e normalizado pela própria `transition` (VBELN canônico). Qualquer falha **no nosso processamento** de um 201 vai para `INCERTO` (D12), nunca para `CRIADO`, `ERRO_TECNICO` ou retry (`TODO(decisão #3)`). |
| D11 ✅ | Divergência na conferência do snapshot (evento novo na §4) | Novo evento **`CONFERENCIA_DIVERGENTE`**: `ENVIANDO` → `ERRO_TECNICO`, ator `worker`, sem justificativa, com `detalhe` (parcela, campo). A conferência roda **depois** de `WORKER_PEGOU` e **antes** do CSRF e do marcador, então é garantido que nada foi enviado. Consequência: `LIBERAR_REENVIO` reenvia o mesmo snapshot, e a conferência diverge de novo; a saída é `CANCELAR` e resubmeter. **Alerta:** divergência indica bug no cálculo, então gera métrica + log `ERROR` (D15); a saída documentada no `docs/RUNBOOK.md` é cancelar e resubmeter. **Versão do algoritmo diferente da atual:** envia **sem** conferir, com `conferencia=pulada_versao` no `detalhe` do `WORKER_PEGOU` e log `WARNING` (aprovado). |
| D12 ✅ | Falha no nosso processamento depois de ler o status (evento novo na §4) | Novo evento **`FALHA_APOS_RESPOSTA`**: `ENVIANDO` → `INCERTO`, ator `worker`. Vale para qualquer exceção depois de ler o status: 201 com JSON inválido, `SalesContract` ausente ou fora do formato, erro no parser ou na transição. **Nunca** `ERRO_TECNICO` nem retry. O `response_body` é gravado **cru**: a coluna passa a ser `TEXT` (limite de 1 MiB, truncamento registrado), mais `response_json` `JSONB` só quando o corpo for JSON válido, porque JSON inválido não cabe em `JSONB`. A exceção não listada é **dividida pelo marcador**: **`FALHA_NAO_CLASSIFICADA_ANTES_ENVIO`** (sem `request_sent_at` commitado) → `ERRO_TECNICO`, e **`FALHA_NAO_CLASSIFICADA_APOS_ENVIO`** (com marcador) → `INCERTO`. A matriz passa de 21 para **25** transições e de 15 para **19** eventos (feito na 2.1b). |
| D13 ✅ | Teste de caos | Exceção injetada em **cada ponto** entre o commit do marcador e o commit do resultado: antes do POST, durante o POST, depois de ler o status, no parse, na transição, na gravação da submissão, do evento e do job, e no commit final. Nos testes com fakes, o ponto é um gancho enumerado. Na integração, o processo é derrubado de verdade e o lock é recuperado. Em **nenhum** caso o contrato volta para `NA_FILA` nem o job é reagendado: o destino é `INCERTO`, direto (`FALHA_APOS_RESPOSTA`/`FALHA_NAO_CLASSIFICADA`) ou via `LOCK_EXPIRADO_COM_ENVIO`. |
| D14 ✅ | `contract_snapshots` imutável | `REVOKE UPDATE, DELETE` no papel da aplicação, como em `contract_events`. O teste de integração tenta atualizar e espera erro. |
| D15 ✅ | Métricas e alertas atrás de uma porta | Porta **`Metrics`** em `application/ports.py` com `incrementar(evento: str, **labels)`. **Adapter da Fase 2:** log JSON nível `ERROR` com o campo fixo **`alert`** (`conferencia_divergente`, `contrato_incerto`, `erro_tecnico`), o que já permite alerta no Zabbix por padrão de log. **Fase 5:** adapter Prometheus atrás da mesma porta, sem mexer no worker nem nos casos de uso. **Teste:** cada transição para `INCERTO` ou `ERRO_TECNICO` e cada `CONFERENCIA_DIVERGENTE` chamam a porta **exatamente uma vez** (fake que conta as chamadas; nenhuma outra transição chama). O adapter de log tem teste próprio: JSON, nível `ERROR`, `alert` com um dos três valores, labels sem dado sensível. |
| D10 ✅ | Testes de integração | `testcontainers[postgres]` como dependência de dev, com marcador `integration` fora do `pytest` padrão e um job `integration` novo no CI, **check obrigatório** do branch. Os casos centrais são a concorrência de dois workers com `SKIP LOCKED` e o **fencing do D4**. |

## Ordem de execução

`2.0` (quando o metadata chegar) → `2.1` → `2.1b` (matriz §4 com os eventos novos) → `2.2` → `2.3` → `2.4` → `2.5` → `2.6` → `2.7` → `2.8`
→ `2.9` → `2.10` → `2.11` → `2.12` (smoke, com autorização) → `2.13`.
TDD em domínio e aplicação. Commits pequenos e push na `develop` a cada tarefa, com CI verde.

---

## Tarefa 2.0 — `metadata.xml` corrigido (bloqueada pelo item 2)

- Conferir `git diff docs/sap/metadata.xml` (esperado: só `&` → `&amp;`) e fazer o commit.
- Remover `_AMP_SOLTO` e o escape em `tests/unit/domain/_referencias_sap.py`.
- Teste novo: o `metadata.xml` é XML bem formado (`ET.parse` sem pré-processamento).

**Arquivos:** △ `docs/sap/metadata.xml`, △ `backend/tests/unit/domain/_referencias_sap.py`,
✱ `backend/tests/unit/domain/test_metadata_xml.py`

## Tarefa 2.1 — Settings da Fase 2 — ✅ FEITA

Novas configs, validadas no startup e cobertas por teste:
- **Settings por processo:** o secret do SAP vai **só para o worker**; a API recebe só o do banco.
  - `ApiSettings`: `APP_ENV`, `LOG_LEVEL` e `DATABASE_URL`.
  - `WorkerSettings`: o mesmo mais toda a config SAP (guard DEV × PRD, credenciais) e a do worker.
  - A regra "em qas/prd, credencial só vem da fonte de arquivo efetiva" passa a valer para cada
    credencial da classe: `database_url` nas duas, `sap_user`/`sap_pass` só no worker.
- `DATABASE_URL`: `SecretStr`, `postgresql+asyncpg://`. Nunca é logado.
- `SAP_DECIMAL_AS_STRING`: default `true` (`TODO(decisão #4)`).
- `SAP_MAX_TENTATIVAS` (5) e `SAP_LOCK_TIMEOUT_S` (600).
  - O guard calcula o prazo por tentativa, 4 × (connect + read), e exige lock > prazo + 60 (D4).
- `WORKER_POLL_INTERVAL_S` (2).

**Arquivos:** △ `backend/app/settings.py`, △ `backend/app/api/deps.py`, △ `backend/app/main.py`,
△ `backend/app/entrypoints/worker.py` (carrega `WorkerSettings`, fail-closed), △ `backend/tests/conftest.py`,
△ `backend/tests/unit/test_settings*.py`, △ `backend/tests/unit/test_health.py`,
△ `backend/tests/unit/test_entrypoints.py`, △ `infra/.env.example`,
△ `infra/compose.dev.yml` (API só com o banco; worker com banco e SAP),
△ `.github/workflows/ci.yml` (smoke do job `images` com a config nova)

## Tarefa 2.1b — Matriz da §4 com os eventos novos (D11, D12) — ✅ FEITA

Feita a pedido do dono do projeto, antes da aprovação do resto do plano, por ser só domínio:
- **ARCHITECTURE §4:** 25 linhas, com `CONFERENCIA_DIVERGENTE` e
  `FALHA_NAO_CLASSIFICADA_ANTES_ENVIO` (→ `ERRO_TECNICO`) e `FALHA_APOS_RESPOSTA` e
  `FALHA_NAO_CLASSIFICADA_APOS_ENVIO` (→ `INCERTO`).
  - A tabela de exceções ganhou a linha "qualquer outra exceção".
  - Entraram os parágrafos de falha pós-resposta e de conferência do snapshot.
- **Código:** `enums.py` com 19 eventos e `states.py` com a `MATRIZ` de 25 linhas.
- **Testes:**
  - o parser aceita qualquer `**Total: N**`, e o `test_enums` confere que o N bate com as linhas;
  - o exaustivo cobre 8 × 19 pares;
  - a garantia "de `ENVIANDO`, só `FALHA_ANTES_POST` e `LOCK_EXPIRADO_SEM_ENVIO` voltam à fila"
    continua valendo.
- **Runbook:** criado o `docs/RUNBOOK.md`, com a entrada de `CONFERENCIA_DIVERGENTE`.

## Tarefa 2.2 — Portas da aplicação — ✅ FEITA

`Protocol`s em `application/ports.py`, sem SQLAlchemy nem httpx:
- `Clock`
- `UnitOfWork` (transação e repositórios)
- `ContractRepo` (get/lock/save com `version`)
- `OutboxRepo` (`pegar_proximo` SKIP LOCKED, `reagendar`, `concluir`, `expirados`)
- `SubmissionRepo` (`registrar_envio`, `registrar_resposta`, `existe_para_tentativa`)
- `EventRepo` (`anexar`)
- `SapContractGateway` (`fetch_csrf`, `criar_contrato(bytes) -> RespostaSap`, que levanta a
  exceção do httpx sem reinterpretar)
- `Metrics` (`incrementar(evento: str, **labels)`, D15)

Mais os tipos de resultado (`RespostaSap`, `MensagemSap`).

**Arquivos:** ✱ `backend/app/application/ports.py`, ✱ `backend/tests/unit/application/__init__.py`,
✱ `backend/tests/unit/application/fakes.py` (fakes em memória das portas, usados nos testes de caso de uso),
✱ `backend/app/observability/metricas.py` (adapter de log da porta `Metrics`, D15),
✱ `backend/tests/unit/test_metricas.py`

## Tarefa 2.3 — Modelo relacional + migração Alembic

- **Tabelas** (§6): `contracts`, `contract_snapshots` (D6′, append-only), `contract_events`,
  `contract_submissions` (com `snapshot_id`, `response_body TEXT` e `response_json JSONB`, D12) e
  `outbox_jobs` (com `snapshot_id`).
  - Dinheiro e quantidade em `NUMERIC`, instantes em `TIMESTAMPTZ`, `payload` e `request_body` em
    `JSONB`, `status` com `CHECK` nos 8 valores do enum.
- **Constraints:**
  - `UNIQUE (idempotency_key)`;
  - `UNIQUE (pedido_sysfertil) WHERE pedido_sysfertil IS NOT NULL AND status NOT IN ('ERRO_NEGOCIO','CANCELADO')`;
  - FK de `contract_events`, `contract_submissions` e `outbox_jobs` para `contracts`.
- **Índice** em `outbox_jobs (run_after)` só para jobs pendentes (`WHERE concluido_em IS NULL`).
  O predicado não pode usar `now()`, porque o Postgres exige função `IMMUTABLE` em índice parcial;
  `locked_until` é comparado na query, com o horário do `Clock` como parâmetro (D5).
- `outbox_jobs.lock_token UUID` (fencing, D4); `contract_submissions.request_body BYTEA`,
  `request_sha256` e `request_json JSONB` opcional (D7).
- **Append-only:** `contract_events` e `contract_snapshots` recebem `REVOKE UPDATE, DELETE` no
  papel da aplicação (D14).
- **Testes:** `alembic upgrade head` → `downgrade base` → `upgrade head` no testcontainers.

**Arquivos:** ✱ `backend/alembic.ini`, ✱ `backend/migrations/env.py`,
✱ `backend/migrations/versions/0001_contratos_outbox.py`, ✱ `backend/app/infrastructure/db/__init__.py`,
✱ `backend/app/infrastructure/db/modelos.py`, ✱ `backend/tests/integration/__init__.py`,
✱ `backend/tests/integration/conftest.py` (container Postgres por sessão),
✱ `backend/tests/integration/test_migracoes.py`, △ `backend/pyproject.toml` (testcontainers, marcador)

## Tarefa 2.4 — Repositórios + Unit of Work (SQLAlchemy async)

- Implementações das portas da 2.2.
- `pegar_proximo` usa `SELECT … FOR UPDATE SKIP LOCKED LIMIT n`.
- `save` do contrato faz lock otimista por `version`.
- `pedido_sysfertil` vazio é gravado como `NULL` (§6).
- Serialização do snapshot conforme D6′: `Contract` → JSON canônico → `Contract.criar` de volta.
  O round-trip é exato, e uma propriedade Hypothesis confere `desserializar(serializar(c)) == c`.

**Testes de integração:**
- dois workers concorrentes nunca pegam o mesmo job;
- job com lock expirado volta a ser elegível;
- conflito de `version` levanta erro;
- **fencing (D4):** o worker A perde o lock por expiração, o worker B recupera
  (`LOCK_EXPIRADO_COM_ENVIO` → `INCERTO`), e o commit 3 de A não grava nada (`WHERE lock_token`)
  e loga `WARNING`;
- a unique parcial de `pedido_sysfertil` barra duplicidade e aceita após `ERRO_NEGOCIO`/`CANCELADO`;
- decimais fazem round-trip exato.

**Arquivos:** ✱ `backend/app/infrastructure/db/repos.py`, ✱ `backend/app/infrastructure/db/uow.py`,
✱ `backend/app/infrastructure/db/serializacao.py`, ✱ `backend/tests/integration/test_repos.py`,
✱ `backend/tests/integration/test_outbox_concorrencia.py`

## Tarefa 2.5 — Client SAP (httpx) com CSRF e log allowlist

- **Sessão:** `httpx.AsyncClient` com Basic Auth lido do secret, `saml2=disabled`, `sap-client`,
  timeouts connect 5 s / read 90 s e **`follow_redirects=False` explícito**, com teste.
- **Prazo total** da tentativa com `asyncio.timeout` (D4); estourar o prazo depois do marcador
  segue a classificação conservadora.
- **CSRF:** `GET {base}` com `x-csrf-token: Fetch`, na mesma sessão e com os mesmos cookies. O token
  fica em cache.
- **POST:**
  - `POST {base}/CriaContrato` com `content=to_json(payload)`, nunca `json=`;
  - `Content-Type: application/json;IEEE754Compatible=true` quando `SAP_DECIMAL_AS_STRING`.
- **403 com `x-csrf-token: Required`:** refaz o fetch e repete o POST **uma vez**, na mesma tentativa (§4).
- **Log allowlist:** uma linha por chamada, com **só** `method`, URL sem query, `status`,
  `duration_ms`, `correlation_id` e `contract_id`.
  - O teste captura os logs e falha se aparecer qualquer outra chave, header ou trecho do body,
    inclusive em `DEBUG`.
- **Testes com respx:**
  - CSRF ok e cacheado;
  - 403 CSRF → refetch + 1 reenvio;
  - 403 CSRF duas vezes: o client devolve a resposta e a classificação decide;
  - `Authorization` nunca nos logs;
  - a URL logada não tem query.

**Arquivos:** ✱ `backend/app/infrastructure/sap/client.py`, ✱ `backend/tests/unit/infrastructure/sap/test_client.py`

## Tarefa 2.6 — Parser de erros e de sucesso do SAP

- **Erro:** `error.code`, `error.message` e `error.details[]` (`code`, `message`, `target`) viram
  uma lista de `MensagemSap`.
  - O `target` é mapeado para o `path` do domínio quando possível (ex.: `to_Item(…)/Material` →
    `to_Item[i].Material`). Quando não é possível, ele é mantido cru.
- **Sucesso (201):** `SalesContract` + header `sap-messages` (warnings).
- **Fixtures:** exemplos sintéticos agora. Com o smoke, entram fixtures reais anonimizadas de DEV em
  `tests/contract/`.
- `TODO(decisão #3)` até a 1ª resposta real.

**Arquivos:** ✱ `backend/app/infrastructure/sap/respostas.py`, ✱ `backend/tests/unit/infrastructure/sap/test_respostas.py`,
✱ `backend/tests/contract/__init__.py`, ✱ `backend/tests/contract/fixtures/` (vazio até o smoke)

## Tarefa 2.7 — Classificação de resultado (pura, no gate de mutação)

- Função pura: resultado do POST (exceção httpx **ou** status + headers + corpo) → `TransitionEvent`
  + detalhe estruturado (`error_class`, `status`).
- **CSRF (D3):** rede (`Connect*`) ou `5xx` → `FALHA_ANTES_POST` (retry); `401`/`403` →
  `SAP_4XX_TECNICO` sem retry, com alerta.
- **`3xx` depois do POST** → `FALHA_APOS_RESPOSTA` → `INCERTO` (linha 7 da §4).
- Precedência exata da §4:
  1. 5xx;
  2. 403 CSRF;
  3. `{401,403,404,405,415}` técnico;
  4. `{400,409,422}` negócio;
  5. outro 4xx com `error.details` → negócio;
  6. outro 4xx sem `error.details` → técnico.
- Exceções: `ConnectError`/`ConnectTimeout` → antes do POST; `WriteError`/`WriteTimeout` → conexão
  caída após POST (conservador); `ReadTimeout` → timeout após POST; `ReadError`/`RemoteProtocolError`
  → conexão caída após POST.
- **Qualquer exceção não listada**, decidida pelo marcador `request_sent_at` commitado:
  sem marcador → `FALHA_NAO_CLASSIFICADA_ANTES_ENVIO` → `ERRO_TECNICO`; com marcador →
  `FALHA_NAO_CLASSIFICADA_APOS_ENVIO` → `INCERTO`. Há teste para os dois lados da fronteira.
- **Resposta lida, mas o nosso processamento falhou → `FALHA_APOS_RESPOSTA` → `INCERTO`** (D12).
  Há um teste para cada variante:
  - 201 com corpo vazio, corpo que não é JSON, JSON sem `SalesContract` e `SalesContract` `null`,
    numérico, com letras, com mais de 10 dígitos ou só com zeros;
  - exceção no parser e exceção na `transition`.
  - Em todas, o `response_body` fica gravado e nenhuma leva a `ERRO_TECNICO` ou a retry.
- **Testes:**
  - leem as duas tabelas da §4 do `.md` e conferem com o código;
  - exaustivo por status 100–599;
  - propriedade: nenhuma entrada depois do POST produz evento que leve a `NA_FILA`.

**Arquivos:** ✱ `backend/app/infrastructure/sap/classificacao.py`, ✱ `backend/tests/unit/infrastructure/sap/test_classificacao.py`,
△ `backend/tests/unit/domain/_referencias_arquitetura.py` (parser das tabelas de classificação)

## Tarefa 2.8 — Casos de uso (TDD, com fakes)

- **`submit_contract` (D1, D6′):**
  - valida pelo domínio e calcula as parcelas;
  - **congela o snapshot** com a versão do algoritmo;
  - aplica `transition(RASCUNHO|ERRO_NEGOCIO, SUBMETER)`;
  - na **mesma transação**, grava o snapshot, o evento e o job (com `snapshot_id`).
- **`process_outbox_job` (D2):**
  - pega o job e aplica `WORKER_PEGOU`;
  - carrega o snapshot e **confere** as parcelas (recalcula com a mesma versão e compara); se
    divergir, `CONFERENCIA_DIVERGENTE` → `ERRO_TECNICO`, sem CSRF nem POST, com
    `Metrics.incrementar("conferencia_divergente", ...)` (D11, D15); com versão diferente, pula a conferência
    (`conferencia=pulada_versao` + `WARNING`);
  - faz o fetch de CSRF (falha → `FALHA_ANTES_POST` ou `_ESGOTOU` conforme `attempts`);
  - grava a submissão (commit);
  - faz o POST;
  - classifica;
  - aplica `transition`, grava o evento e atualiza a submissão e o job.
  - Um `ConnectError` **depois** do commit da submissão continua sendo `FALHA_ANTES_POST` (§4).
- **`recover_expired_locks`:** job com lock expirado vira `LOCK_EXPIRADO_SEM_ENVIO` (sem submissão
  na tentativa) ou `LOCK_EXPIRADO_COM_ENVIO` (com submissão), ator `system`.
- **Testes:** cada linha da classificação ponta a ponta com fakes; nenhum caminho com body enviado
  reagenda o job; toda transição grava evento; toda tentativa de POST grava submissão; o relógio vem
  da porta.
- **Métricas (D15):** toda transição para `INCERTO` ou `ERRO_TECNICO` e todo
  `CONFERENCIA_DIVERGENTE` chamam `Metrics.incrementar` exatamente uma vez. Um teste parametrizado
  percorre todos os caminhos do `process_outbox_job` e do `recover_expired_locks` e confere a
  contagem, zero nos demais.
- **`LIBERAR_REENVIO`:** o job novo reusa o `snapshot_id`. O teste confere que o `request_body`
  da 2ª tentativa é **byte a byte** igual ao da 1ª, mesmo com o algoritmo atual trocado por um fake
  que calcula outra coisa.
- **Caos (D13):** para cada ponto de injeção entre o commit do marcador e o commit do resultado,
  uma exceção é levantada ali; depois o `recover_expired_locks` roda. Asserts: o contrato **nunca**
  volta a `NA_FILA`, o job **nunca** é reagendado, e o estado final é `INCERTO`.
- **Mutação:** os três arquivos entram no gate.

**Arquivos:** ✱ `backend/app/application/submit_contract.py`, ✱ `backend/app/application/process_outbox_job.py`,
✱ `backend/app/application/recover_expired_locks.py`, ✱ `backend/app/application/snapshot.py`
(serialização e conferência do snapshot), ✱ `backend/tests/unit/application/test_submit_contract.py`,
✱ `backend/tests/unit/application/test_caos.py`, ✱ `backend/tests/unit/application/test_snapshot.py`,
✱ `backend/tests/unit/application/test_process_outbox_job.py`, ✱ `backend/tests/unit/application/test_recover_expired_locks.py`

## Tarefa 2.9 — Loop do worker

- `_executar` do `entrypoints/worker.py`:
  - `configure_logging` antes do `Settings`;
  - loop com `recover_expired_locks` e `process_outbox_job`;
  - espera de `WORKER_POLL_INTERVAL_S` quando a fila está vazia;
  - `correlation_id` do job propagado aos logs.
- Parada limpa em SIGTERM/SIGINT: termina a tentativa em curso e não pega job novo.
- Teste de integração: dois workers reais contra Postgres e SAP fake (respx). Cada job é processado
  exatamente uma vez.
- **Caos real (D13):** o worker é derrubado (`os._exit`) em cada ponto depois do commit do marcador,
  e um segundo worker recupera o lock. O contrato termina em `INCERTO` via
  `LOCK_EXPIRADO_COM_ENVIO` e nunca volta a `NA_FILA`.

**Arquivos:** △ `backend/app/entrypoints/worker.py`, △ `backend/tests/unit/test_entrypoints.py`,
✱ `backend/tests/integration/test_worker.py`, ✱ `backend/tests/integration/test_caos.py`

## Tarefa 2.10 — Readiness e saúde do SAP

- **`/health/ready` da API não depende do SAP:** só o check `db` (`SELECT 1`), pelo mecanismo
  existente de `readiness_checks`.
- **`/health/sap`**, informativo e fora do ready: idade do último fetch de CSRF bem-sucedido do
  worker, lido de uma linha de heartbeat no banco (a API não chama o SAP). Sempre responde `200`
  com `ok`/`atrasado`, sem derrubar o roteamento do Traefik.
- **Alerta:** heartbeat atrasado chama `Metrics.incrementar` (D15), e o Zabbix alerta pelo log.

**Arquivos:** △ `backend/app/main.py`, △ `backend/app/api/health.py`,
✱ `backend/app/infrastructure/db/heartbeat.py`, △ `backend/tests/unit/test_health.py`

## Tarefa 2.11 — CI

- Job `integration`: testcontainers com o Docker do runner, `pytest -m integration`. É **check
  obrigatório** do branch (D10); a proteção do branch é configurada pelo dono do projeto no GitHub.
- `[tool.mutmut]`: `only_mutate` ganha `infrastructure/sap/classificacao.py` e os três casos de uso.
  A seleção de testes ganha `tests/unit/application/`.
- O gate de cobertura continua em `domain` + `application` ≥ 90%.

**Arquivos:** △ `.github/workflows/ci.yml`, △ `backend/pyproject.toml`

## Tarefa 2.12 — Smoke real em DEV (só com autorização explícita a cada execução)

- `backend/scripts/smoke_dev.py`:
  - monta o contrato do `payload_exemplo` pelo caminho completo (`submit_contract` →
    `process_outbox_job`) contra o SAP **DEV**;
  - o guard DEV × PRD do settings fica ativo;
  - antes do POST, pede confirmação digitada;
  - gera `PedidoSysFertil` e `PurchaseOrderByCustomer` **únicos por execução**, com prefixo
    `SMOKE-<timestamp>`, para não bater na unique nem ser confundido com dado real.
- Com as respostas reais:
  - grava fixtures anonimizadas em `tests/contract/fixtures/`;
  - fecha as decisões **#3** (formato 201/erros), **#4** (decimal string × número), **#9** (data
    omitida × `null`) e **#13** (formato do VBELN) no código, no ARCHITECTURE §14 e no
    `PERGUNTAS-ABERTAS.md`.

**Arquivos:** ✱ `backend/scripts/smoke_dev.py`, ✱ `backend/tests/contract/test_respostas_reais.py`,
△ `docs/ARCHITECTURE.md`, △ `docs/PERGUNTAS-ABERTAS.md`

## Tarefa 2.13 — Fechamento da Fase 2

- **Checks:** ruff, mypy `--strict`, import-linter, pytest unitário e de integração, cobertura
  ≥ 90% em domain/application, gate de mutação, checks do front e CI verde.
- **Revisão:** subagente novo sobre o diff da Fase 2, com foco em reenvio indevido, marcador de
  submissão, transações, log allowlist e segredos. Achados vão para o dono do projeto sem correção.
- **Relatório:** o que foi entregue e as decisões ainda abertas. A Fase 3 só começa com pedido explícito.

---

## Resumo de arquivos

**Novos (✱):**
- **app:** `application/{ports,snapshot,submit_contract,process_outbox_job,recover_expired_locks}.py`,
  `observability/metricas.py`,
  `infrastructure/db/{__init__,modelos,repos,uow,serializacao,heartbeat}.py` e
  `infrastructure/sap/{client,respostas,classificacao}.py`.
- **migrações:** `alembic.ini`, `migrations/env.py` e `migrations/versions/0001_contratos_outbox.py`.
- **testes unitários:** `tests/unit/application/*` e
  `tests/unit/infrastructure/sap/test_{client,respostas,classificacao}.py`.
- **testes de integração:** `tests/integration/*`.
- **testes de contrato:** `tests/contract/*`.
- **scripts e testes avulsos:** `scripts/smoke_dev.py` e `tests/unit/domain/test_metadata_xml.py`.

**Alterados (△):**
- **backend:** `app/settings.py`, `app/entrypoints/worker.py`, `app/main.py`, `pyproject.toml`,
  `app/domain/{enums,states,installments}.py` (eventos novos e `ALGORITMO_PARCELAS`) e
  os helpers de teste `tests/unit/domain/_referencias_{sap,arquitetura}.py`.
- **infra e CI:** `infra/.env.example`, `infra/compose.dev.yml` e `.github/workflows/ci.yml`.
- **docs:** `docs/sap/metadata.xml`, `docs/ARCHITECTURE.md` e `docs/PERGUNTAS-ABERTAS.md`.
