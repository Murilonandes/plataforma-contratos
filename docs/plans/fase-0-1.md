# Plano — Fase 0 (Fundação) + Fase 1 (Domínio)

> **Para agentes executores:** SKILL OBRIGATÓRIA: use `superpowers:subagent-driven-development` (recomendado) ou `superpowers:executing-plans` para executar tarefa a tarefa. Marque cada `- [ ]` conforme concluir.

**Objetivo Fase 0:** monorepo (`backend/` + `frontend/` + `infra/`) com toolchain, containers, health, logging estruturado e CI verde, sem regra de negócio.

**Objetivo Fase 1:** domínio puro (`backend/app/domain/`) espelhando o `$metadata` do serviço `ZAPI_CONTRATO_VENDAS` — VOs em `@dataclass(frozen=True)` com validação própria, parcelas por maior resto (resíduo nas **primeiras** parcelas), máquina de estados completa (matriz em §4 do ARCHITECTURE.md), e mapper OData em `app/infrastructure/sap/mapper.py` (formato SAP é infra; a função é pura e testada nesta fase com golden file).

**Arquitetura:** monólito modular hexagonal. `domain` ← `application` ← `infrastructure`/`api`, com `import-linter` garantindo a regra no CI (inclui proibir `pydantic` em `app.domain`). Backend Python 3.12 (uv/ruff/mypy strict/pytest/hypothesis). Frontend React 19 + Vite + TS strict + ESLint + Vitest, Node 22 LTS (Node 20 EOL abr/2026). Postgres 16 via compose (só sobe na Fase 2, mas o compose já sai pronto na Fase 0).

**Regras invioláveis (CLAUDE.md + correções desta rodada):**
- `Decimal` no Python, `NUMERIC` no banco. Nunca `float` em dinheiro/quantidade.
- `app/domain/` puro: sem FastAPI, SQLAlchemy, httpx, **nem `pydantic`**. Pydantic vive só em `app/api/` (schemas de entrada/saída).
- Domínio em **`@dataclass(frozen=True)`** com validação própria; erros de domínio carregam o **path do campo** (ex.: `contract.items[0].requested_quantity`).
- Mensagens de validação em **português**, apontando o path do campo.
- Payload OData: strings vazias como `""` (nunca `null`); datas `YYYY-MM-DD`; campos `Computed` (`SalesContract`, `SalesContractItem`, `ConditionUUID`) **nunca enviados**.
- **`StatusBlock` é escrito pelo NOSSO backend, sempre `"06"`, no mapper.** O domínio não tem esse campo no input. Omiti-lo criaria contrato desbloqueado no SAP — teste explícito no mapper.
- Parcelas: método do maior resto — `Porcentagem` (4 casas) soma `100.0000`; `Valor` (2 casas) soma exatamente o total. **Resíduo distribuído nas primeiras parcelas** (1 unidade por parcela, começando pela primeira, até zerar).
- Decisões abertas (ARCHITECTURE.md §14) viram `TODO(decisão #N)` — sem valor inventado.
- **Não inventar hostnames SAP**: só `s4-dev.brfertil.com.br` é conhecido; PRD/QAS vêm por config obrigatória (`SAP_PRD_HOSTS`), fail-closed.

---

## Escopo & premissas

- **Ambiente do dev**: Windows 11, shell `bash` disponível (via Git Bash). Comandos usam paths POSIX. Docker Desktop instalado.
- **Gerenciador front**: `pnpm`. **Gerenciador backend**: `uv`.
- **Postgres**: só é usado a partir da Fase 2; a Fase 0 apenas garante que o `compose.dev.yml` sobe.
- **Decisões abertas** ficam como `TODO(decisão #N)` no código com referência à seção 14 do ARCHITECTURE.md. Nenhum valor "chutado".
- **`StatusBlock`**: o mapper **sempre** grava `"06"` no payload enviado ao SAP. O domínio não aceita esse campo. Teste explícito no mapper: para qualquer `Contract`, `payload["StatusBlock"] == "06"`.
- **Guard SAP fail-closed**: `SAP_PRD_HOSTS` (lista) é obrigatório; se ausente/vazio o app não sobe. `APP_ENV=prd` exige host de `SAP_BASE_URL ∈ SAP_PRD_HOSTS`. `APP_ENV≠prd` exige host `∉ SAP_PRD_HOSTS`. Nenhum host default é assumido.
- **Domínio sem Pydantic**: VOs em `@dataclass(frozen=True)` com validação em `__post_init__` e helpers dedicados. Erros de domínio incluem `field_path` (ex.: `"items[0].requested_quantity"`).
- **`IEEE754Compatible` / decimal como string** (decisão #4): na Fase 1 o mapper produz `Decimal`; a serialização final (número vs string) é da Fase 2. Testes usam `Decimal` na comparação.
- **Cobertura ≥ 90%** aplicada a `app.domain` **e** `app.application` (medidas em conjunto). Sem meta numérica em `app.api`/`app.infrastructure`.
- **CI**: rodado pelo GitHub Actions. Testes de container/e2e ficam para fases posteriores.

---

## Estrutura de arquivos completa (novos = ✱, modificados = △)

### Raiz do repo
- ✱ `.gitignore`
- ✱ `.editorconfig`
- ✱ `docs/plans/fase-0-1.md` (este arquivo)
- △ `docs/ARCHITECTURE.md` (apenas se surgir decisão adicional; a princípio, não mexer)

### Backend (`backend/`)
Config & toolchain:
- ✱ `backend/pyproject.toml` (uv, ruff, mypy, pytest, coverage, import-linter, hypothesis)
- ✱ `backend/.python-version` (`3.12`)
- ✱ `backend/uv.lock` (gerado por `uv lock`)
- ✱ `backend/README.md` (comandos essenciais)
- ✱ `backend/Dockerfile` (multi-stage; `CMD` decide entre `api` e `worker`)
- ✱ `backend/.dockerignore`

Código de aplicação (Fase 0):
- ✱ `backend/app/__init__.py`
- ✱ `backend/app/settings.py` (pydantic-settings + guard `APP_ENV × SAP_HOST`)
- ✱ `backend/app/main.py` (factory FastAPI, monta routers, middleware de correlation id)
- ✱ `backend/app/entrypoints/__init__.py`
- ✱ `backend/app/entrypoints/api.py` (uvicorn programático)
- ✱ `backend/app/entrypoints/worker.py` (stub que registra "worker vazio", loga e sai — corpo real na Fase 2)
- ✱ `backend/app/observability/__init__.py`
- ✱ `backend/app/observability/logging.py` (structlog JSON + processors)
- ✱ `backend/app/observability/middleware.py` (correlation id + logger bind)
- ✱ `backend/app/api/__init__.py`
- ✱ `backend/app/api/health.py` (`/health/live`, `/health/ready`)
- ✱ `backend/app/api/deps.py` (skeleton — settings dependency)
- ✱ `backend/app/domain/__init__.py` (vazio na Fase 0; preenchido na Fase 1)
- ✱ `backend/app/application/__init__.py` (vazio, com docstring)
- ✱ `backend/app/infrastructure/__init__.py` (vazio, com docstring)
- ✱ `backend/app/infrastructure/sap/__init__.py` (vazio na Fase 0; mapper entra na Fase 1)

Testes Fase 0:
- ✱ `backend/tests/__init__.py`
- ✱ `backend/tests/conftest.py`
- ✱ `backend/tests/unit/__init__.py`
- ✱ `backend/tests/unit/test_settings.py`
- ✱ `backend/tests/unit/test_settings_sap_prd_hosts.py` (matriz APP_ENV × SAP_BASE_URL × SAP_PRD_HOSTS)
- ✱ `backend/tests/unit/test_logging.py`
- ✱ `backend/tests/unit/test_health.py`
- ✱ `backend/tests/unit/test_correlation_id.py`
- ✱ `backend/tests/unit/test_import_contracts.py` (dispara import-linter programaticamente)

Código de aplicação (Fase 1 — todos novos):
- ✱ `backend/app/domain/errors.py` (`DomainError`, `ValidationError(field_path, message)`, `InstallmentsError`, `InvalidTransitionError`)
- ✱ `backend/app/domain/money.py` (quantize helpers para BRL, quantidade e percentual)
- ✱ `backend/app/domain/enums.py` (`ContractStatus`, `PartnerFunction`, `ConditionType`, `LongTextId`, `Language`, `Origin`) — sem `StatusBlock` (o valor vive no mapper)
- ✱ `backend/app/domain/contract.py` (**`@dataclass(frozen=True)`** dos VOs: `Header`, `Item`, `Partner`, `Installment`, `PricingElement`, `Text`, `Contract`) — validação em `__post_init__`; **sem `pydantic`**
- ✱ `backend/app/domain/rules.py` (validações agregadas de negócio; devolve `list[ValidationError]` com `field_path`)
- ✱ `backend/app/domain/installments.py` (`calcular_parcelas(total, weights, datas) -> list[Installment]`; maior resto real — sort por fracionário desc, tie por índice asc)
- ✱ `backend/app/domain/states.py` (`Transition(atual, evento) -> proximo`, matriz completa da §4; exige `ator` e, quando marcado, `justificativa`)
- ✱ `backend/app/infrastructure/sap/mapper.py` (`to_odata_payload(contract) -> dict[str, Any]`; **sempre** grava `StatusBlock="06"`)

Testes Fase 1:
- ✱ `backend/tests/unit/domain/__init__.py`
- ✱ `backend/tests/unit/domain/test_money.py`
- ✱ `backend/tests/unit/domain/test_enums.py`
- ✱ `backend/tests/unit/domain/test_contract_vos.py` (dataclass validation + field_path nos erros)
- ✱ `backend/tests/unit/domain/test_rules.py`
- ✱ `backend/tests/unit/domain/test_installments.py` (unit + property tests com Hypothesis)
- ✱ `backend/tests/unit/domain/test_states.py`
- ✱ `backend/tests/unit/infrastructure/__init__.py`
- ✱ `backend/tests/unit/infrastructure/sap/__init__.py`
- ✱ `backend/tests/unit/infrastructure/sap/test_mapper.py` (golden file: `docs/sap/payload_exemplo.json` atualizado)

### Frontend (`frontend/`)
- ✱ `frontend/package.json`
- ✱ `frontend/pnpm-lock.yaml`
- ✱ `frontend/.nvmrc` (`22`)
- ✱ `frontend/tsconfig.json` (strict on)
- ✱ `frontend/tsconfig.node.json`
- ✱ `frontend/vite.config.ts`
- ✱ `frontend/vitest.config.ts`
- ✱ `frontend/eslint.config.js` (flat config)
- ✱ `frontend/.prettierrc.json`
- ✱ `frontend/.gitignore`
- ✱ `frontend/index.html`
- ✱ `frontend/src/main.tsx`
- ✱ `frontend/src/App.tsx` (placeholder — "Plataforma de Contratos")
- ✱ `frontend/src/App.test.tsx`
- ✱ `frontend/src/vite-env.d.ts`
- ✱ `frontend/src/setupTests.ts`
- ✱ `frontend/Dockerfile` (multi-stage: build com node, serve com nginx-alpine)
- ✱ `frontend/nginx.conf`
- ✱ `frontend/.dockerignore`

### Infra (`infra/`)
- ✱ `infra/compose.dev.yml` (postgres + api + worker + web)
- ✱ `infra/.env.example`
- ✱ `infra/postgres/init.sql` (só cria o database `contratos`)

### CI (`.github/`)
- ✱ `.github/workflows/ci.yml` (backend + frontend + segurança)
- ✱ `.github/CODEOWNERS` (opcional — pular na Fase 0 se não houver equipe)
- ✱ `.github/dependabot.yml`

**Total novo: 63 arquivos.** Modificados nesta fase (Tarefa 0.0): `docs/ARCHITECTURE.md` e `docs/sap/payload_exemplo.json`.

---

## Ordem de execução

Sequencial, uma tarefa por vez, com review e commit ao final de cada uma. Sem paralelismo em arquivos compartilhados (`pyproject.toml`, `settings.py`, CI, ARCHITECTURE.md). Fase 0 começa em 0.0 (docs de referência) e termina em 0.12 (CI verde). Fase 1 só começa depois do fechamento formal da Fase 0.

Cada tarefa segue TDD onde couber: teste vermelho → mínimo pra passar → teste verde → refactor → commit convencional.

---

# FASE 0 — FUNDAÇÃO

## Tarefa 0.0 — Atualizar docs de referência (bloqueante, requer aprovação humana ANTES da 0.1)

**Motivo:** o resto do plano depende dessas correções serem a fonte da verdade. Os diffs abaixo são **propostas** — se algum ponto discordar, corrigimos antes de aplicar.

**Arquivos modificados:**
- △ `docs/ARCHITECTURE.md` (§4 matriz de estados; §8 desempate/CodTaxa/FieldControl)
- △ `docs/sap/payload_exemplo.json` (sequência de `Porcentagem`)

### Diff proposto 1 — `docs/ARCHITECTURE.md` §4 (substituir o diagrama ASCII + regras)

Substituir a seção `## 4. Máquina de estados` inteira (do diagrama até "reconciliação de `INCERTO` vira automática") por:

```markdown
## 4. Máquina de estados

Todas as transições exigem `ator` (uuid Entra ID, "worker", ou "sistema"). Transições marcadas com "sim" na coluna `justif.` exigem justificativa em texto livre gravada em `contract_events`. Toda transição gera uma linha em `contract_events` (quem, quando, de/para, detalhe, justificativa se aplicável).

**Matriz de transições:**

| De             | Evento                    | Para          | Ator         | Justif. | Observação                                                 |
|---             |---                        |---            |---           |---      |---                                                         |
| RASCUNHO       | SUBMETER                  | NA_FILA       | usuário      | não     | Cria job no outbox                                         |
| RASCUNHO       | CANCELAR                  | CANCELADO     | usuário      | sim     |                                                            |
| NA_FILA        | WORKER_PEGOU              | ENVIANDO      | worker       | não     | Grava `request_sent_at` só quando o POST vai efetivamente sair (subestado interno do adapter) |
| NA_FILA        | CANCELAR                  | CANCELADO     | admin        | sim     |                                                            |
| ENVIANDO       | SAP_201                   | CRIADO        | worker       | não     | Guarda `sap_contract_number`                               |
| ENVIANDO       | SAP_4XX                   | ERRO_NEGOCIO  | worker       | não     | Terminal até vendedor corrigir                             |
| ENVIANDO       | FALHA_ANTES_POST          | NA_FILA       | worker       | não     | `attempts < N` (DNS, connect, CSRF fetch)                  |
| ENVIANDO       | FALHA_ANTES_POST_ESGOTOU  | ERRO_TECNICO  | worker       | não     | `attempts ≥ N`                                             |
| ENVIANDO       | TIMEOUT_APOS_POST         | INCERTO       | worker       | não     | Grava `request_sent_at` + `last_error`                     |
| ENVIANDO       | SAP_5XX_APOS_POST         | INCERTO       | worker       | não     | LUW pode ter commitado; tratado como incerto               |
| ERRO_NEGOCIO   | SUBMETER                  | NA_FILA       | usuário      | não     | Após correção                                              |
| ERRO_NEGOCIO   | CANCELAR                  | CANCELADO     | usuário      | sim     |                                                            |
| ERRO_TECNICO   | LIBERAR_REENVIO           | NA_FILA       | admin        | sim     |                                                            |
| ERRO_TECNICO   | CANCELAR                  | CANCELADO     | admin        | sim     |                                                            |
| INCERTO        | RECONCILIAR_PARA_CRIADO   | CRIADO        | admin        | sim     | Conferiu VA43; grava `sap_contract_number` + justificativa |
| INCERTO        | LIBERAR_REENVIO           | NA_FILA       | admin        | sim     | Conferiu VA43 e não achou; libera reenvio                  |
| INCERTO        | CANCELAR                  | CANCELADO     | admin        | sim     |                                                            |

Estados terminais: `CRIADO`, `CANCELADO`. Nenhuma transição sai deles.

Qualquer par `(estado, evento)` fora desta matriz é `InvalidTransitionError`.

**Nota sobre `request_sent_at`:** o campo é gravado no exato momento em que o `httpx` inicia o envio do body do POST. É o marcador que permite classificar timeouts como "antes" (não gravou) vs "depois" (gravou) do POST. Necessário para reconciliação manual em VA43 correlacionar o incerto com o contrato SAP eventualmente criado.

Futuro: se liberarem leitura (serviço standard `API_SALES_CONTRACT_SRV` filtrando por `PurchaseOrderByCustomer`), a reconciliação de `INCERTO` vira automática.
```

### Diff proposto 2 — `docs/ARCHITECTURE.md` §8 (três correções)

**2a. Substituir** o bullet:
```markdown
- `Porcentagem` tem 4 casas e soma exatamente `100.0000`. `Valor` tem 2 casas e soma exatamente o total. O resíduo vai para a última parcela.
```
**por**:
```markdown
- `Porcentagem` tem 4 casas e soma exatamente `100.0000`. `Valor` tem 2 casas e soma exatamente o total. O **desempate (resíduo)** é distribuído nas **primeiras** parcelas: uma unidade (`0.0001` em `%`, `0.01` em `BRL`) por parcela, começando pela primeira, até o resíduo zerar. Aplicado igual para `%` e para `valor`.
```

**2b. Adicionar** ao final da seção "Formato:" um bullet novo:
```markdown
- **Obrigatoriedade real** é definida pelo FieldControl do SAP, não apenas pelo `Nullable="false"` do `$metadata`. O domínio trata como opcional (valor `""` no mapper) todos os campos de cabeçalho/item cuja obrigatoriedade depende de FieldControl: `SalesOffice`, `SalesGroup`, `SDDocumentReason`, `IncotermsClassification`, `IncotermsLocation1`, `CustomerPaymentTerms`, `Culture`, `Plant`, `PurchaseOrderByCustomer`, `CustomerPurchaseOrderDate`, `SalesContractValidityEndDate`, `NotaInternaCli`, `PedidoSysFertil` — até haver definição por organização de vendas (`TODO(decisão FieldControl)`).
```

**2c. Substituir** o bullet:
```markdown
**Defaults:** `StatusBlock = "06"` na criação (bloqueado), definido pelo servidor e não pelo cliente.
```
**por**:
```markdown
**Defaults:**
- `StatusBlock = "06"` na criação (bloqueado). "Servidor" aqui = **o nosso backend**: o mapper sempre grava `"06"` no payload enviado ao SAP. O domínio não aceita `StatusBlock` como input (o campo não existe nos VOs). Omitir esse campo cria contrato desbloqueado no SAP — teste no mapper garante presença.
- **`CodTaxa` é opcional** (integração futura com HedgeSistema, decisão #7). O domínio aceita `""` e o mapper serializa `""`. Nunca `null`.
```

### Diff proposto 3 — `docs/sap/payload_exemplo.json`

Trocar apenas a sequência de `Porcentagem` (resíduo vai pra primeira, não pra última):

```diff
   "to_FormPag": [
-    { "Parcela": 1, "TransactionCurrency": "BRL", "Porcentagem": 33.3333, "Valor": 7766.52, "Data": "2026-09-04", "FormPag": "K" },
-    { "Parcela": 2, "TransactionCurrency": "BRL", "Porcentagem": 33.3333, "Valor": 7766.52, "Data": "2026-10-04", "FormPag": "K" },
-    { "Parcela": 3, "TransactionCurrency": "BRL", "Porcentagem": 33.3334, "Valor": 7766.51, "Data": "2026-11-03", "FormPag": "K" }
+    { "Parcela": 1, "TransactionCurrency": "BRL", "Porcentagem": 33.3334, "Valor": 7766.52, "Data": "2026-09-04", "FormPag": "K" },
+    { "Parcela": 2, "TransactionCurrency": "BRL", "Porcentagem": 33.3333, "Valor": 7766.52, "Data": "2026-10-04", "FormPag": "K" },
+    { "Parcela": 3, "TransactionCurrency": "BRL", "Porcentagem": 33.3333, "Valor": 7766.51, "Data": "2026-11-03", "FormPag": "K" }
   ],
```

Observação: o `Valor` já está distribuído "nas primeiras" (`7766.52, 7766.52, 7766.51`, resíduo de 2 centavos nas duas primeiras). Só o `%` precisa mudar.

### Passos de execução

- [x] **Passo 1** — Confirmar com o humano os diffs (feito em 3 rodadas de review; matriz final tem 21 transições).
- [x] **Passo 2** — Aplicar os diffs a `docs/ARCHITECTURE.md` §4/§6/§8/§14 e a `docs/sap/payload_exemplo.json`.
- [x] **Passo 3** — Commit único (junto com as atualizações do plano refletindo o novo escopo).

> Diff efetivamente aplicado difere dos "diffs propostos" acima (rodadas 1-3 refinaram a matriz para 21 linhas, com precedência de 4xx, lock recovery, e FieldControl Mandatory). Ver `git log docs/ARCHITECTURE.md` para o texto final.

---

## Tarefa 0.1 — Bootstrap do repo

**Arquivos:**
- Criar: `.gitignore`, `.editorconfig`

**Passos:**

- [ ] **Passo 1** — Verificar que está em `git` inicializado no branch `develop`.

```bash
git status && git rev-parse --abbrev-ref HEAD
```
Esperado: branch `develop`. Se não estiver, `git checkout -b develop`.

- [ ] **Passo 2** — Criar `.gitignore` na raiz.

Conteúdo mínimo:
```
# Python
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
htmlcov/
.coverage
coverage.xml

# Node
node_modules/
dist/
.vite/

# IDE / OS
.idea/
.vscode/
.DS_Store
Thumbs.db

# Secrets & env
.env
.env.*
!.env.example
infra/secrets/
```

- [ ] **Passo 3** — Criar `.editorconfig`.

```
root = true

[*]
indent_style = space
indent_size = 4
end_of_line = lf
charset = utf-8
trim_trailing_whitespace = true
insert_final_newline = true

[*.{ts,tsx,js,jsx,json,yml,yaml,md}]
indent_size = 2

[Makefile]
indent_style = tab
```

- [ ] **Passo 4** — Commit.

```bash
git add .gitignore .editorconfig
git commit -m "chore: bootstrap gitignore e editorconfig"
```

---

## Tarefa 0.2 — Toolchain backend (`pyproject.toml`)

**Arquivos:**
- Criar: `backend/pyproject.toml`, `backend/.python-version`, `backend/README.md`

**Passos:**

- [ ] **Passo 1** — Consultar `context7` para versões atuais de `fastapi`, `pydantic`, `pydantic-settings`, `structlog`, `hypothesis` antes de fixar.

- [ ] **Passo 2** — Criar `backend/.python-version` com `3.12`.

- [ ] **Passo 3** — Criar `backend/pyproject.toml` com:
  - `[project]`: nome `plataforma-contratos`, versão `0.1.0`, python `>=3.12,<3.13`
  - `dependencies`: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `structlog`, `httpx`, `sqlalchemy[asyncio]`, `alembic`, `asyncpg`
  - `[project.optional-dependencies].dev`: `pytest`, `pytest-asyncio`, `pytest-cov`, `hypothesis`, `respx`, `ruff`, `mypy`, `import-linter`, `types-*` conforme necessário
  - `[project.scripts]`: `plataforma-api = "app.entrypoints.api:main"`, `plataforma-worker = "app.entrypoints.worker:main"`
  - `[tool.ruff]` com `line-length = 100`, `select = ["E","F","I","B","UP","S","BLE","PT","SIM"]`, `target-version = "py312"`
  - `[tool.ruff.lint.per-file-ignores]` liberando `S101` (assert) em `tests/`
  - `[tool.mypy]` com `strict = true`, `python_version = "3.12"`, `plugins = ["pydantic.mypy"]`, `disallow_any_generics = true`
  - `[tool.pytest.ini_options]` com `asyncio_mode = "auto"`, `addopts = "--strict-markers -ra"`, `testpaths = ["tests"]`. **Não** fixar `--cov` no `addopts` — o gate roda em comando separado (abaixo)
  - `[tool.coverage.run]` `source = ["app"]`, `branch = true`; `[tool.coverage.report]` `exclude_lines = ["pragma: no cover", "if TYPE_CHECKING:", "raise NotImplementedError"]`
  - Comando de gate (usado no CI e no fechamento de fase): `uv run pytest --cov=app.domain --cov=app.application --cov-report=term-missing --cov-fail-under=90`. Domínio + aplicação são medidos em conjunto; API/infra ficam de fora do gate
  - `[tool.importlinter]` + contratos (ver Tarefa 0.6)
  - `[tool.hatch.build.targets.wheel]` `packages = ["app"]`
  - `[build-system]` `requires = ["hatchling"]`, `build-backend = "hatchling.build"`

- [ ] **Passo 4** — Criar `backend/app/__init__.py` vazio (necessário pra `uv` reconhecer o pacote antes do lock).

- [ ] **Passo 5** — Rodar `uv sync` no `backend/` para gerar `uv.lock`.

```bash
cd backend && uv sync
```
Esperado: ambiente criado, `uv.lock` gerado.

- [ ] **Passo 6** — Criar `backend/README.md` com a lista de comandos:
```
uv run pytest
uv run pytest --cov=app.domain --cov=app.application --cov-report=term-missing --cov-fail-under=90   # gate de fase
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run lint-imports
```

- [ ] **Passo 7** — Commit.

```bash
git add backend/pyproject.toml backend/.python-version backend/uv.lock backend/README.md backend/app/__init__.py
git commit -m "chore(backend): pyproject com uv/ruff/mypy/pytest/import-linter"
```

---

## Tarefa 0.3 — Settings + guard SAP (fail-closed, sem hostname default)

**Arquivos:**
- Criar: `backend/app/settings.py`
- Criar (teste): `backend/tests/__init__.py`, `backend/tests/unit/__init__.py`, `backend/tests/conftest.py`, `backend/tests/unit/test_settings.py`, `backend/tests/unit/test_settings_sap_prd_hosts.py`

**Contrato do guard (fail-closed, sem defaults):**
- `SAP_BASE_URL`: obrigatório. `HttpUrl` (Pydantic). Ausente/vazio → app não sobe.
- `SAP_PRD_HOSTS`: obrigatório. Lista de hostnames separados por `,` no env (ex.: `s4-prd.brfertil.com.br,s4-prd-dr.brfertil.com.br`). Ausente ou vazio (após trim/split) → app não sobe (`ValueError("SAP_PRD_HOSTS é obrigatório e não pode estar vazio")`).
- Regra:
  - `APP_ENV == "prd"` **exige** `host(sap_base_url) ∈ SAP_PRD_HOSTS`. Fora disso → `ValueError("APP_ENV=prd exige SAP_BASE_URL apontando para host em SAP_PRD_HOSTS")`.
  - `APP_ENV != "prd"` **exige** `host(sap_base_url) ∉ SAP_PRD_HOSTS`. Fora disso → `ValueError("APP_ENV='{env}' não pode apontar para host de produção listado em SAP_PRD_HOSTS")`.
- **Nenhum hostname é hardcoded no código nem na regra.** O único host de conhecimento público é `s4-dev.brfertil.com.br` (usado só no `.env.example` da infra).

**Passos:**

- [ ] **Passo 1** — Escrever `test_settings.py`:
  - Dado `APP_ENV=dev`, `SAP_BASE_URL=https://s4-dev.brfertil.com.br/...`, `SAP_CLIENT=300`, `SAP_PRD_HOSTS=s4-prd.example` (host fictício mas obrigatório) → `Settings()` OK, `settings.app_env == "dev"`, `settings.sap_prd_hosts == ("s4-prd.example",)`.
  - `SAP_BASE_URL` ausente → `ValidationError` de Pydantic.
  - `SAP_PRD_HOSTS` ausente → `ValueError` com mensagem em PT-BR.
  - `SAP_PRD_HOSTS=""` (vazio ou só vírgulas/espaços) → `ValueError`.

- [ ] **Passo 2** — Escrever `test_settings_sap_prd_hosts.py` — matriz completa:

| # | `APP_ENV` | Host de `SAP_BASE_URL` | `SAP_PRD_HOSTS` | Esperado |
|---|---|---|---|---|
| 1 | `prd` | `sap-prd.acme` | `["sap-prd.acme"]` | OK |
| 2 | `prd` | `sap-prd.acme` | `["sap-prd.acme", "sap-prd-dr.acme"]` | OK |
| 3 | `prd` | `sap-outro.acme` | `["sap-prd.acme"]` | falha ("APP_ENV=prd exige...") |
| 4 | `dev` | `sap-dev.acme` | `["sap-prd.acme"]` | OK |
| 5 | `dev` | `sap-prd.acme` | `["sap-prd.acme"]` | falha ("não pode apontar para host de produção...") |
| 6 | `qas` | `sap-qas.acme` | `["sap-prd.acme"]` | OK |
| 7 | `qas` | `sap-prd.acme` | `["sap-prd.acme"]` | falha |
| 8 | `prd` | `sap-prd.acme` | `[]` | falha ("SAP_PRD_HOSTS é obrigatório...") |
| 9 | `dev` | `sap-dev.acme` | `[]` | falha (mesma msg) |

Usar `@pytest.mark.parametrize` para cobrir os 9 casos. Todos os hosts nos testes são fictícios (não referenciar `brfertil.com.br`).

- [ ] **Passo 3** — Rodar `uv run pytest tests/unit/test_settings.py tests/unit/test_settings_sap_prd_hosts.py -v` → esperar falha por `ImportError`.

- [ ] **Passo 4** — Implementar `settings.py`:
  - `class Settings(BaseSettings)` com `model_config = SettingsConfigDict(env_file=None, env_prefix="", case_sensitive=False)`
  - Campos: `app_env: Literal["dev","qas","prd"]`, `sap_base_url: HttpUrl`, `sap_client: str`, `sap_prd_hosts: tuple[str, ...]` (parsed de CSV via `field_validator("sap_prd_hosts", mode="before")` que faz `split(",")`, `strip()`, filtra vazios, e retorna tuple), `sap_timeout_connect_s: float = 5`, `sap_timeout_read_s: float = 90`, `log_level: str = "INFO"`
  - `@field_validator("sap_prd_hosts", mode="after")`: se tupla vazia, `raise ValueError("SAP_PRD_HOSTS é obrigatório e não pode estar vazio")`
  - `@model_validator(mode="after") _valida_ambiente_vs_host`: extrai `sap_base_url.host` (via `urlparse` ou `.host` da `HttpUrl`); aplica a regra descrita acima
  - Sem qualquer default de host no código

- [ ] **Passo 5** — `conftest.py`: fixture `sap_env` que aceita `app_env`, `base_url`, `prd_hosts` e faz `monkeypatch.setenv` de cada — para não poluir o env global entre testes.

- [ ] **Passo 6** — Ajustar `infra/.env.example` (Tarefa 0.10) para conter `SAP_PRD_HOSTS=` **vazio com comentário** dizendo que precisa ser preenchido antes de subir o compose em prd, e `SAP_BASE_URL=https://s4-dev.brfertil.com.br/sap/opu/odata4/sap/zapi_contrato_vendas_o4/srvd_a2x/sap/zapi_contrato_vendas/0001/` (único host conhecido).

- [ ] **Passo 7** — Testes verdes. Commit.

```bash
git add backend/app/settings.py backend/tests/
git commit -m "feat(backend): settings fail-closed (SAP_BASE_URL + SAP_PRD_HOSTS)"
```

---

## Tarefa 0.4 — Logging estruturado + correlation id

**Arquivos:**
- Criar: `backend/app/observability/__init__.py`, `backend/app/observability/logging.py`, `backend/app/observability/middleware.py`
- Criar (teste): `backend/tests/unit/test_logging.py`, `backend/tests/unit/test_correlation_id.py`

**Passos:**

- [ ] **Passo 1** — Escrever `test_logging.py`:
  - `configure_logging(level="INFO")` produz JSON com chaves `timestamp`, `level`, `event`, `logger` no stdout
  - `Authorization` no dict de contexto é redigido para `"***REDACTED***"`

- [ ] **Passo 2** — Escrever `test_correlation_id.py`:
  - request sem header `X-Request-ID` → response tem header `X-Request-ID` gerado (uuid4)
  - request com `X-Request-ID: abc-123` → response tem o mesmo header
  - o logger dentro do handler carrega `correlation_id` bindado

- [ ] **Passo 3** — Rodar testes, ver vermelho.

- [ ] **Passo 4** — Implementar `logging.py`:
  - `configure_logging(level: str) -> None` com processors `structlog.processors.TimeStamper(fmt="iso", utc=True)`, `add_log_level`, `StackInfoRenderer`, `format_exc_info`, um custom `redact_sensitive` (masca `authorization`, `password`, `secret`, `token`) e `JSONRenderer(serializer=orjson.dumps)` (ou `json.dumps` se orjson não entrar nesta fase)
  - Reconfigurar `logging` stdlib para propagar via structlog

- [ ] **Passo 5** — Implementar `middleware.py`:
  - `class CorrelationIdMiddleware(BaseHTTPMiddleware)` que lê `X-Request-ID`, gera se ausente, bindea via `structlog.contextvars.bind_contextvars(correlation_id=...)`, injeta no response, e limpa (`clear_contextvars`) no final.

- [ ] **Passo 6** — Testes verdes. Commit.

```bash
git add backend/app/observability/ backend/tests/unit/test_logging.py backend/tests/unit/test_correlation_id.py
git commit -m "feat(backend): structlog JSON + middleware de correlation id"
```

---

## Tarefa 0.5 — App FastAPI + `/health/live` e `/health/ready`

**Arquivos:**
- Criar: `backend/app/main.py`, `backend/app/api/__init__.py`, `backend/app/api/health.py`, `backend/app/api/deps.py`
- Criar (teste): `backend/tests/unit/test_health.py`

**Passos:**

- [ ] **Passo 1** — Escrever `test_health.py`:
  - `GET /health/live` → 200, body `{"status": "ok"}`
  - `GET /health/ready` → 200 quando checagens passam; retorna 503 quando uma checagem registrada falha

- [ ] **Passo 2** — Ver vermelho.

- [ ] **Passo 3** — Implementar `health.py`:
  - `router = APIRouter(prefix="/health", tags=["health"])`
  - `/live`: sempre 200
  - `/ready`: itera lista `readiness_checks: list[Callable[[], Awaitable[bool]]]` (na Fase 0 a lista está vazia → retorna 200). Estrutura preparada para Fase 2 registrar `check_db` e `check_sap_csrf`.

- [ ] **Passo 4** — Implementar `deps.py` com `def get_settings() -> Settings` cacheado (`@lru_cache`).

- [ ] **Passo 5** — Implementar `main.py`:
  - `def create_app(settings: Settings | None = None) -> FastAPI`
  - Chama `configure_logging(settings.log_level)`
  - `app.add_middleware(CorrelationIdMiddleware)`
  - `app.include_router(health.router)`
  - Não usa `on_event` (deprecated) — usa lifespan async se precisar.

- [ ] **Passo 6** — Testes verdes.

- [ ] **Passo 7** — Criar entrypoints:
  - `app/entrypoints/api.py`: `def main()` chama `uvicorn.run("app.main:create_app", factory=True, host="0.0.0.0", port=8000)`
  - `app/entrypoints/worker.py`: `def main()` loga `"worker stub — Fase 2"` e faz `sys.exit(0)`

- [ ] **Passo 8** — Commit.

```bash
git add backend/app/main.py backend/app/api/ backend/app/entrypoints/ backend/tests/unit/test_health.py
git commit -m "feat(backend): app FastAPI com /health/live e /health/ready"
```

---

## Tarefa 0.6 — Contrato de arquitetura (import-linter)

**Arquivos:**
- Modificar: `backend/pyproject.toml` (seção `[tool.importlinter]`)
- Criar (teste): `backend/tests/unit/test_import_contracts.py`

**Passos:**

- [ ] **Passo 1** — Adicionar em `pyproject.toml`:

```toml
[tool.importlinter]
root_package = "app"

[[tool.importlinter.contracts]]
name = "domain é puro (sem web, orm, http, nem pydantic)"
type = "forbidden"
source_modules = ["app.domain"]
forbidden_modules = [
  "fastapi", "starlette",
  "sqlalchemy", "alembic",
  "httpx",
  "pydantic", "pydantic_settings",
  "app.infrastructure", "app.api", "app.application",
]

[[tool.importlinter.contracts]]
name = "application não conhece web/infra"
type = "forbidden"
source_modules = ["app.application"]
forbidden_modules = ["fastapi", "starlette", "app.api", "app.infrastructure"]

[[tool.importlinter.contracts]]
name = "camadas em layers"
type = "layers"
layers = [
  "app.api | app.infrastructure",
  "app.application",
  "app.domain",
]
```

Nota: `pydantic` fica proibido em `app.domain`. Ele é permitido em `app.api` (schemas de request/response) e em `app.settings` (via `pydantic-settings`). Não é usado em `app.application`.

- [ ] **Passo 2** — Escrever `test_import_contracts.py`:
  - `subprocess.run(["uv","run","lint-imports"], cwd=BACKEND_DIR)` retorna código 0
  - Marcar com `@pytest.mark.slow` se tempo virar problema.

- [ ] **Passo 3** — Rodar `uv run lint-imports` local. Ajustar até passar.

- [ ] **Passo 4** — Commit.

```bash
git add backend/pyproject.toml backend/tests/unit/test_import_contracts.py
git commit -m "chore(backend): import-linter garante domínio puro"
```

---

## Tarefa 0.7 — Dockerfile backend multi-stage

**Arquivos:**
- Criar: `backend/Dockerfile`, `backend/.dockerignore`

**Passos:**

- [ ] **Passo 1** — Consultar `context7` para melhores práticas de imagem uv + Python 3.12.

- [ ] **Passo 2** — Criar `backend/Dockerfile`:
  - Stage `deps`: `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`, copia `pyproject.toml` + `uv.lock`, roda `uv sync --frozen --no-dev`
  - Stage `runtime`: `python:3.12-slim-bookworm`, cria user não-root (`appuser`), copia venv e código, `EXPOSE 8000`, `ENTRYPOINT ["python","-m"]`, `CMD ["app.entrypoints.api"]`
  - Rodar `worker` sobrescreve o `CMD`: `command: ["app.entrypoints.worker"]`

- [ ] **Passo 3** — Criar `backend/.dockerignore` com `.venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `tests/`, `.git`.

- [ ] **Passo 4** — Build local:

```bash
docker build -t plataforma-contratos-backend:dev ./backend
docker run --rm -p 8000:8000 plataforma-contratos-backend:dev
curl -s http://localhost:8000/health/live
```
Esperado: `{"status":"ok"}`.

- [ ] **Passo 5** — Commit.

```bash
git add backend/Dockerfile backend/.dockerignore
git commit -m "chore(backend): Dockerfile multi-stage (api/worker por CMD)"
```

---

## Tarefa 0.8 — Frontend scaffold (Vite + React + TS strict + Vitest)

**Arquivos:**
- Criar: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/vite.config.ts`, `frontend/vitest.config.ts`, `frontend/eslint.config.js`, `frontend/.prettierrc.json`, `frontend/.gitignore`, `frontend/.nvmrc`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/App.test.tsx`, `frontend/src/vite-env.d.ts`, `frontend/src/setupTests.ts`

**Passos:**

- [ ] **Passo 1** — Consultar `context7` para o comando atual `pnpm create vite` com template `react-ts`.

- [ ] **Passo 2** — Criar scaffold:

```bash
cd frontend && pnpm create vite . --template react-ts
pnpm add -D vitest @testing-library/react @testing-library/jest-dom @vitejs/plugin-react jsdom @vitest/coverage-v8 eslint typescript-eslint eslint-plugin-react-hooks eslint-plugin-react-refresh prettier
```

- [ ] **Passo 3** — Ativar `strict: true`, `noUncheckedIndexedAccess: true`, `noImplicitOverride: true` em `tsconfig.json`.

- [ ] **Passo 4** — Configurar `vitest.config.ts` com `environment: "jsdom"`, `setupFiles: ["./src/setupTests.ts"]`, coverage provider `v8`.

- [ ] **Passo 5** — `setupTests.ts`: `import "@testing-library/jest-dom"`.

- [ ] **Passo 6** — Escrever `App.test.tsx`:
  - Renderiza `<App />`, `screen.getByRole("heading", { name: /plataforma de contratos/i })` existe.

- [ ] **Passo 7** — Implementar `App.tsx`: `<h1>Plataforma de Contratos</h1>` (placeholder).

- [ ] **Passo 8** — Adicionar scripts em `package.json`: `"lint": "eslint ."`, `"typecheck": "tsc -b --noEmit"`, `"test": "vitest run"`, `"test:watch": "vitest"`, `"build": "tsc -b && vite build"`.

- [ ] **Passo 9** — Rodar `pnpm lint && pnpm typecheck && pnpm test && pnpm build`. Tudo verde.

- [ ] **Passo 10** — Commit.

```bash
git add frontend/
git commit -m "chore(frontend): scaffold Vite + React + TS strict + Vitest"
```

---

## Tarefa 0.9 — Dockerfile frontend

**Arquivos:**
- Criar: `frontend/Dockerfile`, `frontend/nginx.conf`, `frontend/.dockerignore`

**Passos:**

- [ ] **Passo 1** — Criar `Dockerfile` multi-stage:
  - Stage `build`: `node:22-alpine`, `pnpm i --frozen-lockfile`, `pnpm build`
  - Stage `runtime`: `nginx:1.27-alpine`, copia `dist/` para `/usr/share/nginx/html`, copia `nginx.conf`
  - `EXPOSE 80`

- [ ] **Passo 2** — `nginx.conf`: server single-page (`try_files $uri /index.html`), `gzip on`, headers básicos.

- [ ] **Passo 3** — Build local: `docker build -t plataforma-contratos-web:dev ./frontend` → sobe container e vê a página.

- [ ] **Passo 4** — Commit.

```bash
git add frontend/Dockerfile frontend/nginx.conf frontend/.dockerignore
git commit -m "chore(frontend): Dockerfile multi-stage servindo via nginx"
```

---

## Tarefa 0.10 — `infra/compose.dev.yml`

**Arquivos:**
- Criar: `infra/compose.dev.yml`, `infra/.env.example`, `infra/postgres/init.sql`

**Passos:**

- [ ] **Passo 1** — Criar `.env.example` com `POSTGRES_USER=dev`, `POSTGRES_PASSWORD=dev`, `POSTGRES_DB=contratos`, `APP_ENV=dev`, `SAP_BASE_URL=https://s4-dev.brfertil.com.br/...`, `SAP_CLIENT=300`.

- [ ] **Passo 2** — Criar `postgres/init.sql`: `CREATE DATABASE contratos;` (idempotente — verificar). Alternativa: deixar o `POSTGRES_DB` do postgres official image cuidar disso e não precisar do init.sql. **Decisão**: usar variável `POSTGRES_DB` e não incluir init.sql — remover da lista se seguir esse caminho.

- [ ] **Passo 3** — Criar `compose.dev.yml`:
  - Serviço `db`: `postgres:16-alpine`, env do `.env`, volume nomeado, healthcheck `pg_isready`
  - Serviço `api`: build `../backend`, depends_on `db healthy`, env, portas `8000:8000`, command default (api)
  - Serviço `worker`: mesma imagem, `command: ["app.entrypoints.worker"]`
  - Serviço `web`: build `../frontend`, portas `5173:80`

- [ ] **Passo 4** — Testar:

```bash
cd infra && docker compose -f compose.dev.yml --env-file .env.example up -d db
docker compose -f compose.dev.yml logs db | grep "ready to accept"
docker compose -f compose.dev.yml down
```

- [ ] **Passo 5** — Commit.

```bash
git add infra/
git commit -m "chore(infra): compose.dev.yml com postgres 16 + api + worker + web"
```

---

## Tarefa 0.11 — GitHub Actions CI

**Arquivos:**
- Criar: `.github/workflows/ci.yml`, `.github/dependabot.yml`

**Passos:**

- [ ] **Passo 1** — Criar `ci.yml` com jobs:
  1. `backend`: matrix Python 3.12 → `uv sync --dev` → `uv run ruff check .` → `uv run ruff format --check .` → `uv run mypy app` → `uv run lint-imports` → `uv run pytest` → **gate**: `uv run pytest --cov=app.domain --cov=app.application --cov-report=xml --cov-fail-under=90` (job falha se cobertura em domain+application < 90%)
  2. `frontend`: node 22 + pnpm → `pnpm i --frozen-lockfile` → `pnpm lint` → `pnpm typecheck` → `pnpm test` → `pnpm build`
  3. `security`: `pip-audit`, `pnpm audit --prod`, `gitleaks`
  4. `images`: build backend + frontend, roda `aquasecurity/trivy-action` em modo scan (não bloqueia na Fase 0 — `severity: CRITICAL`, `exit-code: 1`)
  - Trigger: `push` em `develop` e `main`, `pull_request` para os dois.

- [ ] **Passo 2** — Criar `dependabot.yml` cobrindo `pip` (backend), `npm` (frontend), `docker` e `github-actions`.

- [ ] **Passo 3** — Abrir PR de teste (branch `chore/ci-check`) só pra confirmar que os 4 jobs rodam verdes.

- [ ] **Passo 4** — Commit.

```bash
git add .github/
git commit -m "chore(ci): pipeline backend/frontend/segurança/imagens"
```

---

## Tarefa 0.12 — Fechamento Fase 0

- [ ] **Passo 1** — Rodar todos os checks locais:

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run lint-imports && uv run pytest
cd ../frontend && pnpm lint && pnpm typecheck && pnpm test && pnpm build
cd ../infra && docker compose -f compose.dev.yml build
```
Todos verdes.

- [ ] **Passo 2** — Aguardar CI verde no `develop`.

- [ ] **Passo 3** — Rodar `/code-review` (skill `code-review`) e endereçar apontamentos.

- [ ] **Passo 4** — Reportar resumo e pendências ao usuário. **Só então iniciar Fase 1.**

### Registro do fechamento (2026-09-22)

**Validado:** checks locais backend/frontend verdes (63 testes backend, 1 frontend); CI verde no
`05b3618` (run 35769913838) — backend, frontend, security (pip-audit, pnpm audit, gitleaks) e
images (build + smoke das duas imagens + compose `db` healthy + Trivy CRITICAL).

**Escrito mas NÃO validado localmente** (Docker ausente na máquina do dev; só no runner do CI):
- 0.7 `docker build ./backend` + `docker run` + `curl /health/live` — coberto pelo smoke do CI
- 0.9 `docker build ./frontend` + abrir a página — coberto pelo smoke do CI
- 0.10 `docker compose ... up -d db` — coberto pelo CI; `api`/`worker`/`web` via compose **não** foram subidos em lugar nenhum
- Validar os três localmente quando o Docker for reinstalado (antes da Fase 2 / testcontainers)

**Desvios do plano (decididos com o dono do projeto):** React 19 (não 18); Node 22 LTS (Node 20
EOL); nginx 1.30 (1.27 sem suporte); testes de API com `httpx.AsyncClient`+`ASGITransport`
(evita `starlette.testclient`); import-linter in-process; gate de cobertura via
`scripts/coverage_gate.py` (pula só com zero statements); actions fixadas por SHA.

**Correções pós-CI:** `05b3618` — secrets condicional + arquivo > env + `hide_input_in_errors`
(senha vazava em `str(ValidationError)`) + `captureWarnings` e bootstrap de logging no entrypoint.

**Pendências abertas:** Dependabot `uv` falhando (log não lido — sem `gh` na máquina; não bloqueia).

**Code review (Passo 3), 2026-09-22:** revisão de segurança por subagente sem contexto sobre o diff
da Fase 0 (0 CRITICAL, 0 HIGH, 4 MEDIUM, 11 LOW, 6 INFO). Corrigidos antes da Fase 1: 1, 4, 5, 6
(`13cad59`), 2, 3, 8 (`36e7d5b`) e o pacote 12, 13, 14, 16, 18. README em UTF-8 (`c73a88f`).

**Backlog de hardening para a Fase 5** (achados da revisão, decisão do dono do projeto):
- **7** — o guard compara strings: listar em `SAP_PRD_HOSTS` **todos** os nomes DNS, CNAMEs e IPs
  que chegam no PRD (SANs do certificado). Avaliar resolução DNS no startup.
- **9** — frontend em imagem nginx não-root (`nginx-unprivileged`, porta 8080).
- **10** — imagens base (python, uv, node, nginx, postgres) fixadas por digest `@sha256`, com
  Dependabot atualizando.
- **11** — `.venv` da imagem do backend como root e só leitura (hoje é do `appuser`, que pode
  escrever); avaliar remover o pip do runtime.
- **15** — `pip-audit` com dependências da própria ferramenta travadas (lock/hashes).
- **17** — gitleaks: varredura do histórico inteiro em agendamento/`workflow_dispatch` (hoje só o
  range do push/PR); conferir `pull-requests: read` se o repo virar privado.
- **19** — cache GHA `mode=max`: ampliar o `frontend/.dockerignore` (`.npmrc`, `*.pem`,
  `secrets/`) antes de qualquer token de build.
- **Nota do 20** — na Fase 2 a credencial SAP vai **só para o worker**; a API não recebe
  `SAP_USER`/`SAP_PASS` (compose e stack).
- **Tarefa própria: pytest 9 + pytest-asyncio compatível** — subir juntos `pytest` 8→9 e
  `pytest-asyncio` 0.26→1.x (o 0.26 exige `pytest<9`; subir só um não resolve). Conferir
  `asyncio_mode`/`asyncio_default_fixture_loop_scope` e as fixtures async. O Dependabot não
  propõe major (política em `.github/dependabot.yml`): os majors fechados em 2026-09-22 foram
  mypy 2, structlog 26, pytest-cov 7, pytest-asyncio 1.4, @types/node 26, node 26, python 3.14,
  nginx 1.31.
- **Stack Swarm com healthcheck** — a imagem do backend não traz `HEALTHCHECK` (é compartilhada
  api/worker); o stack de PRD declara o healthcheck da `api` (mesmo comando do compose) e nenhum
  healthcheck HTTP no `worker`.

**Segunda revisão (2026-09-22)**, só nos arquivos alterados (1 HIGH, 4 MEDIUM, 7 LOW, 3 INFO).
Corrigidos: log (`9e26141` — bytes, render seguro, regex/chaves, unraisablehook, access log
próprio), guard (`2b26803` — só hostname DNS ASCII nos dois lados) e testes/CI (mutantes
sobreviventes, testes vazios, `pipefail`). Sem terceira revisão completa, por decisão do dono.

**Levado para a Fase 2:**
- O **worker passa a carregar o `Settings`** no startup: o guard DEV×PRD e as regras de
  credencial (arquivo de secret em qas/prd) valem para ele também. Hoje o stub não instancia.

---

# FASE 1 — DOMÍNIO PURO

**Regra geral:** todos os arquivos ficam em `backend/app/domain/`. Nenhum import de `fastapi`, `sqlalchemy`, `httpx`, `starlette`, `pydantic-settings` — o `import-linter` já pega. Testes em `backend/tests/unit/domain/`.

Todas as validações produzem mensagens de erro em **português**, com o campo entre aspas simples: ex.: `"campo 'SalesContractItemText' excede 40 caracteres"`.

**Antes de fechar a Fase 1 (decisão do dono do projeto):** rodar **teste de mutação** no domínio,
no mínimo em `money.py` e `installments.py` (quantização, maior resto, desempate por índice,
validações de peso/total/datas). Todo mutante sobrevivente vira teste novo ou justificativa escrita
(defesa em profundidade), como foi feito na Fase 0.

## Tarefa 1.1 — `money.py`

**Arquivos:**
- Criar: `backend/app/domain/money.py`, `backend/tests/unit/domain/__init__.py`, `backend/tests/unit/domain/test_money.py`

**Passos:**

- [ ] **Passo 1** — Escrever `test_money.py`:
  - `quantize_brl(Decimal("10.005"))` == `Decimal("10.01")` (HALF_UP)
  - `quantize_brl(Decimal("10.004"))` == `Decimal("10.00")`
  - `quantize_qty(Decimal("1.23456"))` == `Decimal("1.235")` (3 casas)
  - `quantize_pct(Decimal("33.33335"))` == `Decimal("33.3334")` (4 casas)
  - Rejeita `float` — passar `float` levanta `TypeError`.

- [ ] **Passo 2** — Implementar:

```python
from decimal import Decimal, ROUND_HALF_UP

BRL = Decimal("0.01")
QTY = Decimal("0.001")
PCT = Decimal("0.0001")

def _ensure_decimal(v: Decimal) -> Decimal:
    if not isinstance(v, Decimal):
        raise TypeError("use Decimal, nunca float")
    return v

def quantize_brl(v: Decimal) -> Decimal:
    return _ensure_decimal(v).quantize(BRL, rounding=ROUND_HALF_UP)

def quantize_qty(v: Decimal) -> Decimal:
    return _ensure_decimal(v).quantize(QTY, rounding=ROUND_HALF_UP)

def quantize_pct(v: Decimal) -> Decimal:
    return _ensure_decimal(v).quantize(PCT, rounding=ROUND_HALF_UP)
```

- [ ] **Passo 3** — Verde, commit.

```bash
git add backend/app/domain/money.py backend/tests/unit/domain/
git commit -m "feat(domain): helpers de quantize para BRL/qty/pct"
```

---

## Tarefa 1.2 — `enums.py`

**Arquivos:**
- Criar: `backend/app/domain/enums.py`, `backend/tests/unit/domain/test_enums.py`

**Passos:**

> **Escopo corrigido (2026-09-23, dono do projeto):** o `$metadata` **não** enumera códigos do SAP
> (`PartnerFunction` é `Edm.String` MaxLength 2 etc.). `enums.py` só tem o que é nosso. Códigos SAP
> viram validação de formato no VO e lista de valores permitidos em config por sales org, validada no
> caso de uso (ARCHITECTURE §8, `TODO(decisão #11)`).
>
> **`Origin` (`WEB`/`API`) não entra aqui:** fica para a **Fase 3** (auth/API define as origens;
> coluna `origin` de `contracts`, ARCHITECTURE §6). Decisão do dono do projeto, 2026-09-23.

- [x] **Passo 1** — Escrever `test_enums.py`:
  - `ContractStatus` tem exatamente `{RASCUNHO, NA_FILA, ENVIANDO, CRIADO, ERRO_NEGOCIO, ERRO_TECNICO, INCERTO, CANCELADO}`
  - `TransitionEvent` tem exatamente os 15 eventos da matriz de 21 transições
  - `ActorKind` tem `{user, admin, worker, system}`
  - estados, eventos e atores conferidos contra a matriz do ARCHITECTURE §4 (o teste lê o `.md`)
  - **nenhum** enum de código SAP (`PartnerFunction`, `ConditionType`, `FormPag`, `LongTextID`, `Language`, `SalesContractType`) nem `StatusBlock` (`"06"` é constante do mapper, Tarefa 1.8)

- [x] **Passo 2** — Implementar como `StrEnum` (Python 3.12).

- [x] **Passo 3** — Verde, commit.

```bash
git add backend/app/domain/enums.py backend/tests/unit/domain/test_enums.py
git commit -m "feat(domain): enums de status, evento e ator"
```

---

## Tarefa 1.3 — `errors.py`

**Arquivos:**
- Criar: `backend/app/domain/errors.py`

**Passos:**

- [ ] **Passo 1** — Definir hierarquia com `field_path` explícito:

```python
class DomainError(Exception):
    """Base para erros de domínio."""

class ValidationError(DomainError):
    """
    Erro de validação de um campo do domínio.
    field_path é o caminho completo até o campo, ex.:
      'items[0].requested_quantity'
      'partners[1].partner_function'
      'installments[2].porcentagem'
    """
    def __init__(self, field_path: str, message: str):
        super().__init__(f"{field_path}: {message}")
        self.field_path = field_path
        self.message = message

class InstallmentsError(DomainError):
    """Erro no cálculo/validação de parcelas."""

class InvalidTransitionError(DomainError):
    def __init__(self, atual: str, evento: str):
        super().__init__(f"transição inválida: {atual} -> {evento}")
        self.atual = atual
        self.evento = evento
```

- [ ] **Passo 2** — Teste rápido em `test_contract_vos.py` (Tarefa 1.4) valida o formato de `field_path`. Sem teste isolado aqui — pequeno demais. Commit.

```bash
git add backend/app/domain/errors.py
git commit -m "feat(domain): exceções de domínio"
```

---

## Tarefa 1.4 — VOs do contrato (`contract.py`) — `@dataclass(frozen=True)`, sem Pydantic

> **Implementado em 2026-09-23 com o desenho revisado pelo dono do projeto** (substitui o esqueleto
> abaixo onde divergir):
> - Validação **acumulada**: `Contract.criar(dados)` valida tudo com um `ErrorCollector` e levanta
>   `DomainValidationError` **uma vez**; os dataclasses frozen só são construídos depois. O
>   `__post_init__` só confere tipos (uso programático). Paths no formato OData
>   (`to_Item[1].Material`, índice base 0); mensagens sem índice.
> - Tabela declarativa `ESPECIFICACOES` por EntityType, conferida contra o `$metadata` por
>   `test_contract_metadata.py` (Mandatory, MaxLength, IsUpperCase, Scale, Precision, tipos; sem
>   Computed nem `StatusBlock`).
> - Normalização: strip em strings; uppercase **só** em `IsUpperCase` (no metadata: só `FormPag`);
>   nada mais. **Sem arredondamento**: escala acima da permitida é erro `decimal_scale` (o exemplo
>   `1.2345 -> 1.235` deste plano não vale mais); `money.py` só canoniza o expoente. `Precision`
>   também validada (`decimal_precision`).
> - Entrada já tipada (`Decimal`, `date`, `int`); conversão do JSON é da API (Fase 3). Campo
>   desconhecido é erro (`unknown_field`), inclusive `StatusBlock` e campos Computed.
> - Sem MaxLength no metadata: teto provisório de 255 em `NotaInternaCli`, `PedidoSysFertil` e
>   `Culture`; `LongText` 1000 provisório (`TODO(decisão #5)`). Nenhuma string do domínio fica
>   sem limite (`test_toda_string_do_dominio_tem_limite`).
> - `docs/sap/metadata.xml` do repo tem `&` sem escape (não é XML bem-formado); o teste escapa antes
>   de parsear, sem alterar o arquivo.

**Arquivos:**
- Criar: `backend/app/domain/contract.py`, `backend/tests/unit/domain/test_contract_vos.py`

**Regras estruturais:**
- Todos os VOs são `@dataclass(frozen=True, slots=True)`.
- `import pydantic` está proibido nesta camada pelo `import-linter`.
- Validação em `__post_init__`, coletando erros e lançando `ValidationError` com `field_path` no primeiro erro. (Alternativa: retornar lista pelo `validate_contract` — a "acumulação" fica em `rules.py`; nos VOs individuais, o primeiro erro já é fatal para manter a instância inválida fora do sistema.)
- Helper interno `_check_str(field_path, valor, *, max_len, required=False)` centraliza normalização (`None → ""`) e validação de tamanho.
- Nomes dos campos em snake_case; o mapping para PascalCase do SAP é do mapper.

**Passos:**

- [ ] **Passo 1** — Ler `docs/sap/metadata.xml` e extrair para cada entity type usado (`SalesContractOP`, `ItemOP`, `PartnerOP`, `FormPagOP`, `PricingElementOP`, `TextOP`):
  - nome do campo
  - `MaxLength` (só relevante para `Edm.String`)
  - `Computed` (via anotação `Core.Computed`)
  - **API-mandatory**: verifica presença da anotação `SAP__common.FieldControl EnumMember="com.sap.vocabularies.Common.v1.FieldControlType/Mandatory"`. Só esses campos ficam `required=True` nos VOs. **Não usar `Nullable`** — todas as strings são `Nullable="false"` no serviço, isso só significa "envie `""`, não `null`".
  - Escrever um pequeno script/teste em `backend/tests/unit/domain/test_metadata_mandatory_extraction.py` que roda o extrator direto no XML e produz a lista canônica; o VO importa essa lista (ou tem uma constante espelho que o teste valida contra o XML). Critério de sanidade: `docs/sap/payload_exemplo.json` **precisa passar** sem `ValidationError` — se falhar, ou o exemplo está errado, ou a anotação foi lida errado.
  Anotar num docstring no topo do arquivo o resumo da extração (não replicar o metadata inteiro).

- [ ] **Passo 2** — Escrever `test_contract_vos.py`:
  - `Header(**dict_valido)` cria instância; `frozen=True` impede mutação (`h.sold_to_party = "x"` → `FrozenInstanceError`)
  - `Header(sold_to_party="")` → `ValidationError`, `err.field_path == "sold_to_party"`, mensagem em PT-BR
  - `Header(sold_to_party="x" * 11)` → `ValidationError`, `err.field_path == "sold_to_party"`, msg "excede 10 caracteres"
  - `Item(requested_quantity=Decimal("1.2345"))` → quantiza pra 3 casas via `quantize_qty` (`1.235`)
  - `Item(requested_quantity=1.23)` (float) → `ValidationError` (via `_ensure_decimal`)
  - `Item(requested_quantity=Decimal("0"))` ou negativo → `ValidationError`, `field_path == "requested_quantity"`
  - `Partner(partner_function="Y1", customer="1002138", supplier="", personnel="", contact_person="")` OK; sem `customer` nem `supplier` nem `personnel` nem `contact_person` → `ValidationError` (a linha precisa ter pelo menos um preenchido)
  - `Text(long_text="x"*40)` OK; `Text(long_text="x"*(N+1))` → erro com `field_path == "long_text"` — MaxLength real por `TODO(decisão #5)`, usar `N = 1000` como placeholder generoso e marcar
  - Erros em VOs filhos, quando construídos dentro de `Contract(items=[...])`, propagam `field_path` com prefixo (ex.: `"items[0].requested_quantity"`). Isso é responsabilidade de `Contract.__post_init__`

- [ ] **Passo 3** — Implementar. Esqueleto:

```python
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from .errors import ValidationError
from .money import quantize_qty, _ensure_decimal

def _check_str(path: str, valor: str | None, *, max_len: int, required: bool = False) -> str:
    v = "" if valor is None else valor
    if required and v == "":
        raise ValidationError(path, "obrigatório")
    if len(v) > max_len:
        raise ValidationError(path, f"excede {max_len} caracteres")
    return v

@dataclass(frozen=True, slots=True)
class Header:
    sales_contract_type: str
    sales_organization: str
    distribution_channel: str
    organization_division: str
    sold_to_party: str
    transaction_currency: str
    sales_office: str = ""
    sales_group: str = ""
    sd_document_reason: str = ""
    incoterms_classification: str = ""
    incoterms_location1: str = ""
    customer_payment_terms: str = ""
    purchase_order_by_customer: str = ""
    customer_purchase_order_date: date | None = None
    sales_contract_validity_end_date: date | None = None
    nota_interna_cli: str = ""      # TODO(decisão #5) MaxLength real
    pedido_sysfertil: str = ""      # TODO(decisão #5) MaxLength real
    cod_taxa: str = ""              # TODO(decisão #7) opcional

    def __post_init__(self) -> None:
        object.__setattr__(self, "sales_contract_type",
            _check_str("sales_contract_type", self.sales_contract_type, max_len=4, required=True))
        object.__setattr__(self, "sales_organization",
            _check_str("sales_organization", self.sales_organization, max_len=4, required=True))
        # ...idem para os demais...
```

Mesmo padrão para `Item`, `Partner`, `Installment`, `PricingElement`, `Text`.

- [ ] **Passo 4** — `class Contract`:
  - Campos: `header: Header`, `items: tuple[Item, ...]`, `partners: tuple[Partner, ...]`, `header_pricing: tuple[PricingElement, ...]`, `installments: tuple[Installment, ...]`, `texts: tuple[Text, ...]` (tuples pra manter `frozen`)
  - `__post_init__`: itera filhos, captura `ValidationError` deles e re-lança com `field_path` prefixado (`"items[0]." + err.field_path`)
  - Sem `StatusBlock` — o campo não existe aqui

- [ ] **Passo 5** — Testes verdes. Commit.

```bash
git add backend/app/domain/contract.py backend/tests/unit/domain/test_contract_vos.py
git commit -m "feat(domain): VOs em dataclass(frozen=True) com field_path nos erros"
```

---

## Tarefa 1.5 — `rules.py`

> **Implementado em 2026-09-23 no desenho acumulado da 1.4** (substitui o esqueleto abaixo):
> - `validar_regras(*, parceiros, itens_recebidos) -> tuple[FieldError, ...]`, pura, sem importar
>   `contract.py`. O `Contract.criar` incorpora o resultado no mesmo `ErrorCollector`: erros de
>   campo e de regra saem juntos numa única `DomainValidationError`.
> - `min_items` em `to_Item` (`{"min": 1}`) quando `to_Item` está ausente, `None` ou vazio. Conta
>   elementos **recebidos** (válidos ou não); `to_Item` que nem é lista só dá `invalid_type`.
> - `duplicate_partner_function` em `to_Partner[j].PartnerFunction` para toda repetição depois da
>   primeira, com o índice real do payload (recebe pares `(path, função)`). Função vazia fica de fora
>   (já é `required`). Comparação exata depois do strip, sem normalizar caixa.
> - `qty > 0` continua nos VOs (1.4).

**Arquivos:**
- Criar: `backend/app/domain/rules.py`, `backend/tests/unit/domain/test_rules.py`

**Passos:**

- [ ] **Passo 1** — Escrever `test_rules.py`:
  - `validate_contract(contract_valido)` retorna `[]`
  - Contrato com dois `Partner` mesmo `PartnerFunction` → `ValidationError(field_path="partners[1].partner_function", message="parceiro duplicado para função 'Y1'")`
  - Contrato sem itens → `ValidationError(field_path="items", message="contrato deve ter ao menos um item")`
  - Testes de qty <= 0 já cobertos em `test_contract_vos.py` (a validação vive lá)

- [ ] **Passo 2** — Implementar `validate_contract(c: Contract) -> list[ValidationError]` que **agrega** erros de negócio inter-campos (o `Contract.__post_init__` já garantiu tipo/tamanho por VO). `rules.py` foca em invariantes que envolvem mais de um campo/objeto.

- [ ] **Passo 3** — Testes verdes. Commit.

```bash
git add backend/app/domain/rules.py backend/tests/unit/domain/test_rules.py
git commit -m "feat(domain): rules de negócio (parceiro único, itens obrigatórios, qty > 0)"
```

---

## Tarefa 1.6 — `installments.py` (maior resto + Hypothesis)

> **Implementado em 2026-09-23 com os requisitos do dono do projeto** (substitui o esqueleto abaixo):
> - `calcular_parcelas(total, pesos, datas) -> tuple[ParcelaCalculada, ...]` (`parcela`,
>   `porcentagem`, `valor`, `data`). Não devolve `Installment`: moeda e `FormPag` são do caso de
>   uso, que monta o `to_FormPag` e passa pelo `Contract.criar`. **`to_FormPag` nunca vem do
>   cliente** (CLAUDE.md, ARCHITECTURE §8); na Fase 1 há um teste de documentação apontando a regra.
> - Maior resto em aritmética inteira (`divmod`), chave explícita `(-resto, índice)`, independente
>   para `%` e valor.
> - Validação acumulada (`DomainValidationError`), paths `total`/`pesos[i]`/`datas[i]`: `total` com a
>   especificação de `Valor`; N de 1 a 36 (`max_items`); peso inteiro de 1 a 10.000
>   (`invalid_weight` com `max`); datas estritamente crescentes (`dates_not_increasing`,
>   `TODO(decisão #12)`).
> - Mínimo pela **cota exata** (`total ≥ 0.01 × soma/menor peso`): `installment_below_minimum` em
>   `total` com `{"minimo_total", "parcelas"}`, mínimo exato (regra monotônica). O teto de peso
>   garante `Porcentagem ≥ 0.0001`.
> - Hypothesis: soma exata, proximidade (piso ou piso + 1 unidade), monotonicidade nos pesos,
>   determinismo, forma/mínimo e fronteira do mínimo. Mutação: gate do CI em `installments.py`.

**Arquivos:**
- Criar: `backend/app/domain/installments.py`, `backend/tests/unit/domain/test_installments.py`

**Passos:**

**Contrato da API:**
- `calcular_parcelas(total: Decimal, weights: list[int], datas: list[date]) -> list[Installment]`
- `weights[i] ≥ 1`. Para parcelas iguais, passe `[1] * n`. Algoritmo é o **maior resto real** (Hare / largest remainder): ordena por parte fracionária desc, tie-break por menor índice.
- Aplica de forma **independente** para `%` e para valor (podem terminar com ordens de distribuição diferentes).

- [ ] **Passo 1** — Escrever testes de exemplo (maior resto real):
  - **Parcelas iguais** (empate → menor índice primeiro):
    - `calcular_parcelas(Decimal("100.00"), [1,1,1], [d1,d2,d3])` → `%`: `33.3334, 33.3333, 33.3333`; `valor`: `33.34, 33.33, 33.33`.
    - `calcular_parcelas(Decimal("23299.55"), [1,1,1], [...])` bate com `payload_exemplo.json` **atualizado**: `%`: `33.3334, 33.3333, 33.3333`; `valor`: `7766.52, 7766.52, 7766.51`.
  - **Pesos desiguais exatos** (sem resíduo):
    - `calcular_parcelas(Decimal("100.00"), [30,70], [d1,d2])` → `%`: `30.0000, 70.0000`; `valor`: `30.00, 70.00`.
    - `calcular_parcelas(Decimal("100.00"), [1,1,1,97], [d1,d2,d3,d4])` → `%`: `1.0000, 1.0000, 1.0000, 97.0000`; `valor`: `1.00, 1.00, 1.00, 97.00`.
  - **Pesos desiguais que geram resíduo** (verifica o sort por fracionário):
    - `calcular_parcelas(Decimal("100.00"), [1,1,1,3], [d1,d2,d3,d4])`:
      - divisão exata `%`: `16.6666..., 16.6666..., 16.6666..., 50.0000`
      - base quantizada down: `16.6666, 16.6666, 16.6666, 50.0000` (soma `99.9998`, resíduo `0.0002`)
      - fracionários: os 3 primeiros empatam (~`0.6667`), o último é `0`. Resíduo distribuído aos 2 menores índices dos empatados.
      - **esperado**: `%`: `16.6667, 16.6667, 16.6666, 50.0000`; verificar valor de forma análoga.
  - `n=1` (`weights=[1]`) → `%` = `100.0000`, valor = total
  - `weights=[]` → `InstallmentsError`
  - `weights=[0, 1]` (peso zero) → `InstallmentsError`
  - `weights=[1, -1]` (peso negativo) → `InstallmentsError`
  - `total <= 0` → `InstallmentsError`
  - `total` como `float` → `InstallmentsError` (via `_ensure_decimal`)
  - `len(datas) != len(weights)` → `InstallmentsError`

- [ ] **Passo 2** — Property tests com Hypothesis:

```python
from hypothesis import given, strategies as st, settings
from decimal import Decimal
from datetime import date

total_st = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("100000000.00"), places=2)
weights_st = st.lists(st.integers(min_value=1, max_value=100), min_size=1, max_size=60)

@given(total=total_st, weights=weights_st)
@settings(max_examples=500)
def test_parcelas_fecham_100_pct_e_total(total, weights):
    datas = [date(2026, 1, 1)] * len(weights)
    parcelas = calcular_parcelas(total=total, weights=weights, datas=datas)
    assert sum(p.porcentagem for p in parcelas) == Decimal("100.0000")
    assert sum(p.valor for p in parcelas) == total
```

- [ ] **Passo 3** — Implementar `calcular_parcelas(total, weights, datas)` com o maior resto real. Aplicar o mesmo esqueleto para `%` e para valor, com unidades e alvos diferentes:

```python
def _distribuir_maior_resto(exact: list[Decimal], unidade: Decimal, alvo: Decimal) -> list[Decimal]:
    """
    exact:  valor exato por parcela (não quantizado)
    unidade: 0.0001 (%) ou 0.01 (BRL)
    alvo:   100.0000 ou total
    retorna: lista quantizada cuja soma == alvo, distribuindo o resíduo por maior resto.
    """
    bases = [v.quantize(unidade, rounding=ROUND_DOWN) for v in exact]
    residuo = int((alvo - sum(bases)) / unidade)  # nº de unidades a distribuir
    fracs = [(v - b, i) for i, (v, b) in enumerate(zip(exact, bases))]
    # sort por fracionário desc, empate por menor índice
    ordem = sorted(fracs, key=lambda x: (-x[0], x[1]))
    resultado = list(bases)
    for k in range(residuo):
        _frac, idx = ordem[k]
        resultado[idx] += unidade
    return resultado

def calcular_parcelas(total: Decimal, weights: list[int], datas: list[date]) -> list[Installment]:
    _ensure_decimal(total)
    if not weights or len(weights) != len(datas):
        raise InstallmentsError("weights e datas devem ter mesmo tamanho > 0")
    if any(w <= 0 for w in weights):
        raise InstallmentsError("todos os weights devem ser inteiros positivos")
    if total <= 0:
        raise InstallmentsError("total deve ser positivo")

    sw = Decimal(sum(weights))
    pct_exact = [Decimal(w) * Decimal(100) / sw for w in weights]
    val_exact = [total * Decimal(w) / sw for w in weights]

    pcts   = _distribuir_maior_resto(pct_exact, PCT, Decimal("100.0000"))
    values = _distribuir_maior_resto(val_exact, BRL, total)

    return [
        Installment(numero=i+1, porcentagem=pcts[i], valor=values[i], data=datas[i])
        for i in range(len(weights))
    ]
```

Notas: para parcelas iguais (`weights=[1]*n`), o fracionário é constante em ambas as camadas, e o tie-break por menor índice reproduz o caso simples "primeiras parcelas". Para `weights` desiguais que dão resultado exato (30/70), o resíduo é `0` e o `sorted` não muda nada.

- [ ] **Passo 4** — Testes verdes (incluindo os 500 exemplos do Hypothesis).

- [ ] **Passo 5** — Commit.

```bash
git add backend/app/domain/installments.py backend/tests/unit/domain/test_installments.py
git commit -m "feat(domain): cálculo de parcelas por maior resto + property tests"
```

---

## Tarefa 1.7 — `states.py` (máquina de estados) — matriz completa da §4 atualizada

> **Implementado em 2026-09-24 com os requisitos do dono do projeto** (substitui o esqueleto abaixo):
> - Fonte única: a tabela do ARCHITECTURE §4. `test_states` lê as 21 linhas (parser compartilhado
>   com `test_enums` em `tests/unit/domain/_referencias_arquitetura.py`) e confere `MATRIZ` campo a
>   campo; o conjunto que exige `sap_contract_number` é o das linhas cuja Observação o cita.
>   Exaustivo 8 × 15: 21 válidos, 99 `InvalidTransitionError` (que vence qualquer dado inválido).
> - `transition(atual, evento, *, ator, justificativa=None, sap_contract_number=None, detalhe=None)
>   -> Transicao`, pura. `Transicao` é plana (`de`, `para`, `evento`, `ator`, `justificativa`,
>   `sap_contract_number`, `detalhe`) e **é** o registro do evento em `contract_events`.
> - **Sem timestamp no domínio:** `occurred_at` (e `contract_id`) são carimbados pelo caso de uso via
>   porta `Clock` (Fase 2/3).
> - Erros acumulados (`DomainValidationError`): `ator.kind` (`actor_not_allowed`),
>   `ator.identifier`, `justificativa` (obrigatória no "sim"; opcional para user/admin no "não";
>   `not_applicable` para worker/system; 10 a 500 após strip), `sap_contract_number` (obrigatório
>   só em `SAP_201`/`RECONCILIAR_PARA_CRIADO`, até 10 dígitos ASCII, `TODO(decisão #13)`;
>   normalizado para o VBELN canônico de 10 dígitos com zeros à esquerda; `not_applicable` nas
>   demais) e `detalhe` (só worker/system; chave snake_case até 40, valor
>   `int`/`str` até 200). Vazio ou só espaços = ausente.
> - `states.py` entrou no mutmut e no gate (zero sobreviventes).

**Arquivos:**
- Criar: `backend/app/domain/states.py`, `backend/tests/unit/domain/test_states.py`

**Contrato:**
- Estados: os 8 listados na §4 atualizada (`RASCUNHO`, `NA_FILA`, `ENVIANDO`, `CRIADO`, `ERRO_NEGOCIO`, `ERRO_TECNICO`, `INCERTO`, `CANCELADO`)
- **15 eventos** na `Event` enum: `SUBMETER`, `WORKER_PEGOU`, `SAP_201`, `SAP_4XX_NEGOCIO`, `SAP_4XX_TECNICO`, `FALHA_ANTES_POST`, `FALHA_ANTES_POST_ESGOTOU`, `TIMEOUT_APOS_POST`, `CONEXAO_CAIDA_APOS_POST`, `SAP_5XX_APOS_POST`, `LOCK_EXPIRADO_SEM_ENVIO`, `LOCK_EXPIRADO_COM_ENVIO`, `RECONCILIAR_PARA_CRIADO`, `LIBERAR_REENVIO`, `CANCELAR`. A matriz tem **21 linhas** porque `SUBMETER`, `LIBERAR_REENVIO` e `CANCELAR` aparecem partindo de estados diferentes.
- Função `transition(atual: ContractStatus, evento: Event, *, ator: Actor, justificativa: str | None = None) -> ContractStatus`
- `ator` **obrigatório em todas** as transições (dataclass `Actor` com `kind: Literal["user","admin","worker","system"]` e `identifier: str`)
- `justificativa: str` **obrigatória** quando a coluna `justif.` da matriz for "sim". Se ausente → `ValidationError("justificativa","obrigatória para a transição {atual} -{evento}-> {proximo}")` (importante: `ValidationError` do `errors.py`, não `InvalidTransitionError`)
- `ator.kind` também é validado contra a coluna "Ator" da matriz. Divergência → `ValidationError("ator", "...")`
- Estados terminais `CRIADO` e `CANCELADO`: qualquer evento partindo deles → `InvalidTransitionError`
- `RASCUNHO -CANCELAR-> CANCELADO`: **não** pede justificativa (é rascunho)
- Transições de recuperação (`LOCK_EXPIRADO_*`) exigem `ator.kind == "system"`

**Passos:**

- [ ] **Passo 1** — Escrever `test_states.py` cobrindo linha a linha a matriz da §4 (uma linha da matriz = pelo menos 2 testes: caminho feliz + variação com justificativa/ator errados). Casos obrigatórios:
  - Cada uma das **21 transições** válidas com ator correto (+ justificativa quando necessário) → retorna o `Para` da matriz
  - `RASCUNHO -CANCELAR-> CANCELADO` **sem** justificativa → OK (não é obrigatória)
  - `NA_FILA -CANCELAR-> CANCELADO` **sem** justificativa → `ValidationError`
  - `RECONCILIAR_PARA_CRIADO` sem `justificativa` → `ValidationError`
  - `RECONCILIAR_PARA_CRIADO` com `ator.kind="worker"` → `ValidationError` (esperado `admin`)
  - `LIBERAR_REENVIO` de `ERRO_TECNICO` sem justificativa → `ValidationError`
  - `WORKER_PEGOU` com `ator.kind="user"` → `ValidationError`
  - `SUBMETER` a partir de `ENVIANDO` → `InvalidTransitionError`
  - `SAP_201` a partir de `NA_FILA` → `InvalidTransitionError`
  - Qualquer evento a partir de `CRIADO` ou `CANCELADO` → `InvalidTransitionError`
  - `ENVIANDO -FALHA_ANTES_POST-> NA_FILA` (retry ainda disponível) OK
  - `ENVIANDO -FALHA_ANTES_POST_ESGOTOU-> ERRO_TECNICO` OK
  - `ENVIANDO -TIMEOUT_APOS_POST-> INCERTO` OK
  - `ENVIANDO -CONEXAO_CAIDA_APOS_POST-> INCERTO` OK
  - `ENVIANDO -SAP_5XX_APOS_POST-> INCERTO` OK
  - `ENVIANDO -SAP_4XX_NEGOCIO-> ERRO_NEGOCIO` OK
  - `ENVIANDO -SAP_4XX_TECNICO-> ERRO_TECNICO` OK
  - `ENVIANDO -LOCK_EXPIRADO_SEM_ENVIO-> NA_FILA` com `ator.kind="system"` OK
  - `ENVIANDO -LOCK_EXPIRADO_COM_ENVIO-> INCERTO` com `ator.kind="system"` OK
  - `LOCK_EXPIRADO_SEM_ENVIO` com `ator.kind="worker"` → `ValidationError` (esperado `system`)
  - `INCERTO -CANCELAR-> CANCELADO` OK (com justificativa)

- [ ] **Passo 2** — Implementar como tabela declarativa:

```python
from dataclasses import dataclass
from typing import Literal
from enum import StrEnum
from .enums import ContractStatus
from .errors import ValidationError, InvalidTransitionError

class Event(StrEnum):
    SUBMETER = "SUBMETER"
    WORKER_PEGOU = "WORKER_PEGOU"
    SAP_201 = "SAP_201"
    SAP_4XX_NEGOCIO = "SAP_4XX_NEGOCIO"
    SAP_4XX_TECNICO = "SAP_4XX_TECNICO"
    FALHA_ANTES_POST = "FALHA_ANTES_POST"
    FALHA_ANTES_POST_ESGOTOU = "FALHA_ANTES_POST_ESGOTOU"
    TIMEOUT_APOS_POST = "TIMEOUT_APOS_POST"
    CONEXAO_CAIDA_APOS_POST = "CONEXAO_CAIDA_APOS_POST"
    SAP_5XX_APOS_POST = "SAP_5XX_APOS_POST"
    LOCK_EXPIRADO_SEM_ENVIO = "LOCK_EXPIRADO_SEM_ENVIO"
    LOCK_EXPIRADO_COM_ENVIO = "LOCK_EXPIRADO_COM_ENVIO"
    RECONCILIAR_PARA_CRIADO = "RECONCILIAR_PARA_CRIADO"
    LIBERAR_REENVIO = "LIBERAR_REENVIO"
    CANCELAR = "CANCELAR"

ActorKind = Literal["user","admin","worker","system"]

@dataclass(frozen=True, slots=True)
class Actor:
    kind: ActorKind
    identifier: str  # oid Entra ID, "worker-<id>", "system"

@dataclass(frozen=True, slots=True)
class _Rule:
    proximo: ContractStatus
    ator_esperado: ActorKind
    justificativa_obrigatoria: bool

_MATRIX: dict[tuple[ContractStatus, Event], _Rule] = {
    (ContractStatus.RASCUNHO,      Event.SUBMETER):                 _Rule(ContractStatus.NA_FILA,      "user",   False),
    (ContractStatus.RASCUNHO,      Event.CANCELAR):                 _Rule(ContractStatus.CANCELADO,    "user",   False),  # rascunho: sem justificativa
    (ContractStatus.NA_FILA,       Event.WORKER_PEGOU):             _Rule(ContractStatus.ENVIANDO,     "worker", False),
    (ContractStatus.NA_FILA,       Event.CANCELAR):                 _Rule(ContractStatus.CANCELADO,    "admin",  True),
    (ContractStatus.ENVIANDO,      Event.SAP_201):                  _Rule(ContractStatus.CRIADO,       "worker", False),
    (ContractStatus.ENVIANDO,      Event.SAP_4XX_NEGOCIO):          _Rule(ContractStatus.ERRO_NEGOCIO, "worker", False),
    (ContractStatus.ENVIANDO,      Event.SAP_4XX_TECNICO):          _Rule(ContractStatus.ERRO_TECNICO, "worker", False),
    (ContractStatus.ENVIANDO,      Event.FALHA_ANTES_POST):         _Rule(ContractStatus.NA_FILA,      "worker", False),
    (ContractStatus.ENVIANDO,      Event.FALHA_ANTES_POST_ESGOTOU): _Rule(ContractStatus.ERRO_TECNICO, "worker", False),
    (ContractStatus.ENVIANDO,      Event.TIMEOUT_APOS_POST):        _Rule(ContractStatus.INCERTO,      "worker", False),
    (ContractStatus.ENVIANDO,      Event.CONEXAO_CAIDA_APOS_POST):  _Rule(ContractStatus.INCERTO,      "worker", False),
    (ContractStatus.ENVIANDO,      Event.SAP_5XX_APOS_POST):        _Rule(ContractStatus.INCERTO,      "worker", False),
    (ContractStatus.ENVIANDO,      Event.LOCK_EXPIRADO_SEM_ENVIO):  _Rule(ContractStatus.NA_FILA,      "system", False),
    (ContractStatus.ENVIANDO,      Event.LOCK_EXPIRADO_COM_ENVIO):  _Rule(ContractStatus.INCERTO,      "system", False),
    (ContractStatus.ERRO_NEGOCIO,  Event.SUBMETER):                 _Rule(ContractStatus.NA_FILA,      "user",   False),
    (ContractStatus.ERRO_NEGOCIO,  Event.CANCELAR):                 _Rule(ContractStatus.CANCELADO,    "user",   True),
    (ContractStatus.ERRO_TECNICO,  Event.LIBERAR_REENVIO):          _Rule(ContractStatus.NA_FILA,      "admin",  True),
    (ContractStatus.ERRO_TECNICO,  Event.CANCELAR):                 _Rule(ContractStatus.CANCELADO,    "admin",  True),
    (ContractStatus.INCERTO,       Event.RECONCILIAR_PARA_CRIADO):  _Rule(ContractStatus.CRIADO,       "admin",  True),
    (ContractStatus.INCERTO,       Event.LIBERAR_REENVIO):          _Rule(ContractStatus.NA_FILA,      "admin",  True),
    (ContractStatus.INCERTO,       Event.CANCELAR):                 _Rule(ContractStatus.CANCELADO,    "admin",  True),
}
assert len(_MATRIX) == 21  # sanity: fica alinhado com a §4 do ARCHITECTURE.md

def transition(atual: ContractStatus, evento: Event, *, ator: Actor, justificativa: str | None = None) -> ContractStatus:
    regra = _MATRIX.get((atual, evento))
    if regra is None:
        raise InvalidTransitionError(atual.value, evento.value)
    if ator.kind != regra.ator_esperado:
        raise ValidationError("ator", f"esperado kind='{regra.ator_esperado}' para {atual}-{evento}, veio '{ator.kind}'")
    if regra.justificativa_obrigatoria and (justificativa is None or justificativa.strip() == ""):
        raise ValidationError("justificativa", f"obrigatória para a transição {atual} -{evento}-> {regra.proximo}")
    return regra.proximo
```

Manter a matriz **em um único lugar** e conferi-la contra a §4 do ARCHITECTURE.md atualizado. Um teste `test_matrix_sincronizada_com_arch_md` pode ler `docs/ARCHITECTURE.md` e conferir contagem/linhas via regex simples (opcional, mas útil).

- [ ] **Passo 3** — Verde, commit.

```bash
git add backend/app/domain/states.py backend/tests/unit/domain/test_states.py
git commit -m "feat(domain): máquina de estados completa (matriz §4, ator+justificativa)"
```

---

## Tarefa 1.8 — `mapper.py` em `app/infrastructure/sap/` (função pura, golden file)

**Arquivos:**
- Criar: `backend/app/infrastructure/sap/__init__.py`, `backend/app/infrastructure/sap/mapper.py`
- Criar (teste): `backend/tests/unit/infrastructure/__init__.py`, `backend/tests/unit/infrastructure/sap/__init__.py`, `backend/tests/unit/infrastructure/sap/test_mapper.py`

**Por que aqui, não em `app/domain/`:** o formato OData (nomes em PascalCase, `to_Item`, chaves específicas do RAP) é detalhe de infra do SAP. Domínio expõe conceito (`Contract`), infra traduz para wire format. Mapper permanece função pura (sem I/O), então dá pra testar exaustivamente aqui na Fase 1.

**Contrato da função:**
- `def to_odata_payload(contract: Contract) -> dict[str, Any]`
- **`StatusBlock`**: **sempre** presente com valor `"06"` (constante `_STATUS_BLOCK_INICIAL = "06"` privada do módulo). Teste explícito garante presença em qualquer input.
- Campos `Computed` (`SalesContract`, `SalesContractItem`, `ConditionUUID`) **nunca** aparecem no dict.
- Strings vazias → `""` (nunca `None`/`null`).
- `date` → ISO `YYYY-MM-DD`.
- `Decimal` permanece `Decimal` no dict (conversão number vs string é da Fase 2 sob `SAP_DECIMAL_AS_STRING` — decisão #4).
- Chaves em **PascalCase** conforme `docs/sap/metadata.xml`.
- Aninhamentos exatos: `to_Item`, `to_Partner`, `to_FormPag`, `to_PricingElement` (header **e** item), `to_Text`. Listas vazias saem como `[]` (nunca omitidas).

**Passos:**

- [ ] **Passo 1** — Criar `app/infrastructure/__init__.py` (docstring: "adapters de I/O; depende de application/domain") e `app/infrastructure/sap/__init__.py` (docstring: "adapter para o serviço ZAPI_CONTRATO_VENDAS").

- [ ] **Passo 2** — Escrever `test_mapper.py`:

```python
import json
from decimal import Decimal
from datetime import date
from pathlib import Path
from app.infrastructure.sap.mapper import to_odata_payload
# ... imports dos VOs

REPO_ROOT = Path(__file__).resolve().parents[5]  # backend/tests/unit/infrastructure/sap -> repo root
PAYLOAD_EXEMPLO = REPO_ROOT / "docs" / "sap" / "payload_exemplo.json"

def _load_exemplo_normalizado() -> dict:
    """Carrega o golden file e converte números para Decimal para comparação exata."""
    raw = json.loads(PAYLOAD_EXEMPLO.read_text(encoding="utf-8"), parse_float=lambda s: Decimal(s), parse_int=lambda s: Decimal(s) if "." in s else int(s))
    # Ajustes: datas viram date; ints puros ficam int (Parcela, RequestedQuantity nesse json é 1 int)
    # ... (helper interno)
    return raw

def test_mapper_bate_com_payload_exemplo():
    contract = _construir_contract_do_exemplo()
    esperado = _load_exemplo_normalizado()
    assert to_odata_payload(contract) == esperado

def test_status_block_sempre_06(contract_qualquer):
    payload = to_odata_payload(contract_qualquer)
    assert payload["StatusBlock"] == "06"

def test_computed_nunca_no_payload(contract_qualquer):
    payload = to_odata_payload(contract_qualquer)
    for chave_proibida in ("SalesContract", "SalesContractItem", "ConditionUUID"):
        assert chave_proibida not in payload
        for item in payload["to_Item"]:
            assert chave_proibida not in item
            for pe in item.get("to_PricingElement", []):
                assert "ConditionUUID" not in pe

def test_string_vazia_nunca_null():
    contract = _contract_com_campos_opcionais_vazios()
    payload = to_odata_payload(contract)
    for opcional in ("SalesOffice", "SalesGroup", "SDDocumentReason", "IncotermsClassification"):
        assert payload[opcional] == ""

def test_listas_vazias_saem_como_lista():
    contract = _contract_sem_pricing_de_header()
    payload = to_odata_payload(contract)
    assert payload["to_PricingElement"] == []

def test_data_ausente_omite_chave():
    """Edm.Date ausente: a chave é OMITIDA do payload — nunca vira "" (isso é só Edm.String)."""
    contract = _contract_com_datas_none()
    payload = to_odata_payload(contract)
    assert "CustomerPurchaseOrderDate" not in payload
    assert "SalesContractValidityEndDate" not in payload

def test_string_ausente_vira_vazio_nao_omite():
    """Contraste explícito com data: Edm.String ausente vira "" e continua no payload."""
    contract = _contract_com_strings_opcionais_vazias()
    payload = to_odata_payload(contract)
    assert payload["SalesOffice"] == ""    # presente, vazio
    assert payload["CustomerPaymentTerms"] == ""  # presente, vazio
```

O helper `_construir_contract_do_exemplo` cria um `Contract` que corresponde 1:1 ao `payload_exemplo.json` **atualizado** (Tarefa 0.0). Passar `parse_float=Decimal` no `json.loads` mantém precisão para comparação exata.

- [ ] **Passo 3** — Implementar `mapper.py`:

```python
from decimal import Decimal
from typing import Any
from app.domain.contract import Contract, Header, Item, Partner, Installment, PricingElement, Text

_STATUS_BLOCK_INICIAL = "06"

def to_odata_payload(contract: Contract) -> dict[str, Any]:
    h = contract.header
    payload: dict[str, Any] = {
        "SalesContractType":              h.sales_contract_type,
        "SalesOrganization":              h.sales_organization,
        "DistributionChannel":            h.distribution_channel,
        "OrganizationDivision":           h.organization_division,
        "SalesOffice":                    h.sales_office,
        "SalesGroup":                     h.sales_group,
        "SDDocumentReason":               h.sd_document_reason,
        "SoldToParty":                    h.sold_to_party,
        "TransactionCurrency":            h.transaction_currency,
        "IncotermsClassification":        h.incoterms_classification,
        "IncotermsLocation1":             h.incoterms_location1,
        "CustomerPaymentTerms":           h.customer_payment_terms,
        "PurchaseOrderByCustomer":        h.purchase_order_by_customer,
        "NotaInternaCli":                 h.nota_interna_cli,
        "PedidoSysFertil":                h.pedido_sysfertil,
        "CodTaxa":                        h.cod_taxa,
        "StatusBlock":                    _STATUS_BLOCK_INICIAL,  # regra do backend
        "to_Item":            [_item(i) for i in contract.items],
        "to_Partner":         [_partner(p) for p in contract.partners],
        "to_FormPag":         [_form_pag(fp) for fp in contract.installments],
        "to_PricingElement":  [_pricing(pe) for pe in contract.header_pricing],
        "to_Text":            [_text(t) for t in contract.texts],
    }
    # Datas (Edm.Date): incluídas SÓ quando presentes. `None` = chave omitida.
    # `""` seria erro no SAP para Edm.Date. Ver §8 do ARCHITECTURE.md e decisão #9.
    if h.customer_purchase_order_date is not None:
        payload["CustomerPurchaseOrderDate"] = _fmt_date(h.customer_purchase_order_date)
    if h.sales_contract_validity_end_date is not None:
        payload["SalesContractValidityEndDate"] = _fmt_date(h.sales_contract_validity_end_date)
    return payload
# helpers _item / _partner / _form_pag / _pricing / _text / _fmt_date...
```

O mesmo padrão vale para datas dentro de `Item` (`ScheduleDate`, `ScheduleDate2`): se `None`, a chave é omitida do dict do item.

- [ ] **Passo 4** — Rodar `uv run pytest tests/unit/infrastructure/sap/ -v`. Iterar até bater o golden file.

- [ ] **Passo 5** — Verificar que `import-linter` continua verde (o mapper importa de `app.domain`, o que é permitido — camadas). Rodar `uv run lint-imports`.

- [ ] **Passo 6** — Commit.

```bash
git add backend/app/infrastructure/ backend/tests/unit/infrastructure/
git commit -m "feat(infra/sap): mapper Contract -> payload OData (StatusBlock=06 sempre)"
```

---

## Tarefa 1.9 — Fechamento Fase 1

- [ ] **Passo 1** — Rodar todos os checks:

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run lint-imports
uv run pytest
uv run pytest --cov=app.domain --cov=app.application --cov-report=term-missing --cov-fail-under=90
```
- Todos verdes.
- Cobertura **combinada em `app.domain` + `app.application` ≥ 90%**. Nesta fase `application/` está vazio, então o gate mede efetivamente `domain/`; ele já entra configurado para as próximas fases não precisarem revisitar CI.

- [ ] **Passo 2** — CI verde no `develop`.

- [ ] **Passo 3** — `/code-review` — endereçar apontamentos. Se houver skill `differential-review` disponível (Trail of Bits), rodar também.

- [ ] **Passo 4** — Reportar ao usuário: resumo do que foi entregue, lista de `TODO(decisão #N)` que ficaram no código, e explicitamente perguntar se pode iniciar a Fase 2. **Não iniciar Fase 2 sem pedido.**

---

# Auto-review (feito pelo autor do plano)

**1. Cobertura do escopo (KICKOFF §3 + correções desta rodada):**

| Requisito Fase 0 | Tarefa |
|---|---|
| Correções em docs antes de codar | **0.0** |
| Monorepo backend/frontend/infra | 0.1, 0.2, 0.8, 0.10 |
| uv + ruff + mypy strict + pytest | 0.2 |
| import-linter (domain puro, **sem pydantic**) | 0.6 |
| pydantic-settings (só em `app.settings`) | 0.3 |
| structlog JSON + correlation id | 0.4 |
| `/health/live` + `/health/ready` | 0.5 |
| Vite + React + TS strict + eslint + vitest | 0.8 |
| Dockerfile multi-stage (api ou worker por CMD) | 0.7, 0.9 |
| `infra/compose.dev.yml` com Postgres 16 | 0.10 |
| GitHub Actions com todos os checks + trivy + gate cov | 0.11 |
| Guard SAP fail-closed (`SAP_BASE_URL` + `SAP_PRD_HOSTS`, sem defaults) | 0.3 |
| Cobertura ≥ 90% em `app.domain` + `app.application` | 0.2, 0.11, 1.9 |

| Requisito Fase 1 | Tarefa |
|---|---|
| VOs espelhando `$metadata` **em `@dataclass(frozen=True)`, sem pydantic** | 1.4 |
| Validações PT-BR com `field_path` (`items[0].requested_quantity`) | 1.3, 1.4, 1.5 |
| Parcelas maior resto **com resíduo nas primeiras parcelas** + property tests | 1.6 |
| Máquina de estados **exata da §4 atualizada** com ator + justificativa obrigatórios | 1.7 |
| Mapper **em `app/infrastructure/sap/`** validado contra `payload_exemplo.json` **atualizado** | 1.8 |
| **`StatusBlock="06"` sempre presente no payload** (teste explícito) | 1.8 |
| Decisões abertas viram `TODO(decisão #N)` (nunca inventar) | 1.4, 1.6, 1.8 |

Nada do escopo ficou fora.

**2. Placeholders:** nenhum "TBD"/"add appropriate handling"/"similar to Task N". Onde há decisão SAP aberta, está marcado como `TODO(decisão #N)` com referência à §14 (ou §8/FieldControl) do ARCHITECTURE.md — intencional, não placeholder.

**3. Consistência de nomes:**
- `calcular_parcelas(total, weights, datas)` — mesma assinatura em 1.6 (maior resto real; parcelas iguais = `weights=[1]*n`)
- `to_odata_payload(contract)` — mesmo nome em 1.8, agora em `app.infrastructure.sap.mapper`
- `transition(atual, evento, *, ator, justificativa=None)` — mesma assinatura em 1.7
- `Installment.porcentagem` / `Installment.valor` — consistente entre 1.4 e 1.6
- `Contract.installments` — mesmo campo em 1.4 e 1.6
- `ValidationError(field_path, message)` — mesma assinatura em 1.3, 1.4, 1.5, 1.7
- `ContractStatus.{RASCUNHO,NA_FILA,ENVIANDO,CRIADO,ERRO_NEGOCIO,ERRO_TECNICO,INCERTO,CANCELADO}` — mesma lista em 1.2 e 1.7
- **Sem `StatusBlock` no domínio** — 1.2 explicita, 1.8 usa constante privada

Nada quebrado.

---

# Confirmações que o humano pediu

- ✅ `states.py` (Tarefa 1.7) implementa **exatamente** a matriz da §4 do ARCHITECTURE.md (21 transições), incluindo `ENVIANDO → NA_FILA` (retry) / `ENVIANDO → ERRO_TECNICO` (retry esgotou), `INCERTO → {CRIADO, NA_FILA, CANCELADO}`, `LOCK_EXPIRADO_{SEM,COM}_ENVIO` (ator `system`), e `SAP_4XX` dividido em `SAP_4XX_NEGOCIO`/`SAP_4XX_TECNICO`.
- ✅ Cobertura ≥ 90% aplicada a `app.domain` + `app.application` (combinadas), configurada no gate de pytest e no CI. Não medida em `api`/`infrastructure`.

---

# Entrega

Plano atualizado em `docs/plans/fase-0-1.md`. Execução (após seu ok):

- **Subagent-Driven, uma tarefa por vez**, com review humana e commit ao final de cada tarefa. Sem paralelismo em arquivos compartilhados (`pyproject.toml`, `settings.py`, CI, `ARCHITECTURE.md`).
- Tarefa 0.0 é bloqueante: só sigo pra 0.1 após você aprovar os 3 diffs propostos (ou me passar as correções neles).
