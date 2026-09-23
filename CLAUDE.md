# CLAUDE.md — Plataforma de Contratos SAP

Esta plataforma cria contratos de venda (ZCON) no SAP S/4HANA pelo serviço OData V4 `ZAPI_CONTRATO_VENDAS`.
**Leia `docs/ARCHITECTURE.md` antes de qualquer tarefa.** Ele é a fonte da verdade do design.

## Stack
- **Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async) + Alembic, httpx, structlog, uv
- **Banco:** PostgreSQL 16. A fila de envio é uma tabela outbox com `FOR UPDATE SKIP LOCKED`, sem Redis
- **Frontend:** React 19 + Vite + TypeScript strict, TanStack Query + Router, React Hook Form + Zod, shadcn/ui + Tailwind, MSAL React
- **Infra:** Docker multi-stage, Docker Swarm + Traefik, GitHub Actions
- **Auth:** Microsoft Entra ID (SPA PKCE no front, validação de JWT e app roles na API)

## Como trabalhar neste repo
1. **Investigue antes de modificar.** Leia os arquivos envolvidos e **liste os arquivos que vai criar/alterar e espere confirmação** antes de editar.
2. Trabalhe **por fase** (seção 13 do ARCHITECTURE.md). Cada fase fecha com testes verdes e CI verde.
3. **TDD no domínio e na aplicação:** o teste vem primeiro, depois o código.
4. Commits pequenos e convencionais (`feat:`, `fix:`, `test:`, `chore:`). Branch `develop` para DEV e `main` para PRD. Nunca commitar direto em `main`.
5. Na dúvida sobre regra de negócio SAP, **pergunte**. Não invente. As pendências estão na seção 14 do ARCHITECTURE.md.

## Regras invioláveis
- **Dinheiro e quantidade:** `Decimal` no Python e `NUMERIC` no banco. **Nunca float.** Isso vale para o JSON enviado ao SAP também.
- **`app/domain/` é puro:** sem FastAPI, SQLAlchemy, httpx ou I/O. O `import-linter` valida isso.
- **Nunca reenviar automaticamente um POST ao SAP que possa ter chegado lá.** Timeout, conexão caída depois do envio ou 5xx depois do envio vão para o estado `INCERTO`. Retry automático só é permitido para falha *antes* do POST (DNS, connect, CSRF fetch).
- **Credenciais SAP** só via Docker secret ou arquivo. Nunca em código, `.env` versionado, log ou `request_body` salvo. O header `Authorization` é sempre redigido.
- **Log do adapter SAP é allowlist:** cada chamada ao SAP loga **só** `method`, URL **sem query string**, `status`, `duration_ms`, `correlation_id` e `contract_id`. **Nunca** headers nem body crus, nem em `DEBUG`. O que precisar de auditoria vai para `contract_submissions` (redigido). A redação por padrão em `app/observability/logging.py` é rede de segurança, não licença para logar mais.
- **Logs são 100% JSON**, inclusive warnings, exceções não tratadas e falha de startup. Entrypoints chamam `configure_logging` antes de instanciar o `Settings`.
- **Payload OData:**
  - strings vazias vão como `""` (nunca `null`)
  - campos `Computed` (`SalesContract`, `SalesContractItem`, `ConditionUUID`) nunca são enviados
  - datas em `YYYY-MM-DD`
- **Parcelas:** calculadas no servidor pelo método do maior resto.
  - `Porcentagem` com 4 casas soma exatamente `100.0000`
  - `Valor` com 2 casas soma exatamente o total
- **`to_FormPag` NUNCA vem do cliente.** A entrada da API/caso de uso é `total` + `pesos` + `datas` + `FormPag`; as parcelas são sempre montadas por `calcular_parcelas` (`app/domain/installments.py`). O schema de entrada da Fase 3 não tem `to_FormPag`.
- **`StatusBlock = "06"`**: o mapper do nosso backend SEMPRE grava `"06"` no payload enviado ao SAP. O domínio não tem esse campo e o cliente não controla. Omitir = contrato desbloqueado.
- Toda transição de estado grava em `contract_events`. Toda tentativa de POST grava em `contract_submissions`.
- A plataforma DEV nunca aponta para o SAP PRD. Há um guard de startup que checa host × `APP_ENV`.

## Referências SAP
- `docs/sap/metadata.xml`: `$metadata` do serviço (tipos, MaxLength, obrigatórios, Computed)
- `docs/sap/payload_exemplo.json`: payload válido de referência
- `docs/sap/smoke_test_referencia.py`: script standalone que já faz CSRF + POST + parse de erro. É referência de comportamento, não código de produção
- Endpoint DEV: `https://s4-dev.brfertil.com.br/sap/opu/odata4/sap/zapi_contrato_vendas_o4/srvd_a2x/sap/zapi_contrato_vendas/0001/` (`sap-client=300`, `saml2=disabled`)

## Comandos
Preencha na Fase 0 e mantenha atualizado:
- backend (em `backend/`): `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy app`, `uv run lint-imports`
- gate de cobertura (em `backend/`): `uv run pytest --cov=app.domain --cov=app.application --cov-report=term-missing` e depois `uv run python scripts/coverage_gate.py`
- frontend (em `frontend/`): `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm format:check`, `pnpm build` (`pnpm e2e` entra na Fase 5)
- local: `docker compose -f infra/compose.dev.yml --env-file infra/.env up` (copiar `infra/.env.example`; `SAP_PRD_HOSTS` é obrigatório)

## Qualidade antes de dizer "pronto"
- `ruff`, `mypy --strict`, `import-linter`, `pytest` e checks do front verdes
- Cobertura ≥ 90% em `domain/` e `application/`
- Mensagens de erro de domínio são testadas por **igualdade exata** (`str(exc.value) == "..."`) ou `match` com `re.escape` e âncoras `^...$`. **Nunca por substring**: `pytest.raises(match=...)` faz `re.search` e deixa mutantes vivos
- Revisão com `/code-review` (ou o agente do `pr-review-toolkit`) antes de abrir PR
- Nenhum aviso pendente do hook `security-guidance`
