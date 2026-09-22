# Kickoff no Claude Code

## 1. Preparar o repo

```bash
mkdir plataforma-contratos && cd plataforma-contratos
# copie para cá: CLAUDE.md e a pasta docs/ deste pacote
git init && git checkout -b develop
git add . && git commit -m "docs: arquitetura e referências SAP"
```

Instale os language servers. Eles fazem o Claude ver erros de tipo logo depois de cada edição:

```bash
npm i -g pyright typescript-language-server typescript
```

## 2. Instalar plugins (marketplace oficial da Anthropic)

O escopo `project` registra os plugins em `.claude/settings.json` (versionado). Quem clonar ainda precisa instalar cada plugin uma vez (o Claude Code avisa e mostra o comando); o `settings.json` garante que todos usem o mesmo conjunto. `.claude/settings.local.json` fica gitignorado.

Rode no terminal (fora da sessão do `claude`):

```bash
claude plugin install superpowers@claude-plugins-official --scope project
claude plugin install feature-dev@claude-plugins-official --scope project
claude plugin install security-guidance@claude-plugins-official --scope project
claude plugin install code-review@claude-plugins-official --scope project
claude plugin install pr-review-toolkit@claude-plugins-official --scope project
claude plugin install context7@claude-plugins-official --scope project
claude plugin install frontend-design@claude-plugins-official --scope project
claude plugin install playwright@claude-plugins-official --scope project
claude plugin install commit-commands@claude-plugins-official --scope project
claude plugin install pyright-lsp@claude-plugins-official --scope project
claude plugin install typescript-lsp@claude-plugins-official --scope project
```

Alternativa: `/plugin install <nome>` dentro da sessão e escolha **Project scope** na tela que abre.

| Plugin | Para que serve aqui |
|---|---|
| `superpowers` | Disciplina de processo: plano escrito → execução por etapas, TDD, debugging sistemático |
| `feature-dev` | Agentes de exploração, arquitetura e review por feature |
| `security-guidance` | Hook que revisa cada edição atrás de vulnerabilidades (credencial SAP, injeção, etc.) |
| `code-review` / `pr-review-toolkit` | Revisão antes do merge: testes, tratamento de erro, tipos |
| `context7` | Docs atualizadas de FastAPI, SQLAlchemy 2, TanStack e MSAL, em vez de API alucinada |
| `frontend-design` | UI com cara de produto, não de template genérico |
| `playwright` | E2E e verificação visual do front |
| `commit-commands` | Commits e PRs padronizados |
| `pyright-lsp` / `typescript-lsp` | Diagnóstico de tipo em tempo real |

Se a skill **brfertil-design** estiver sincronizada da sua conta, o Claude Code usa ela na Fase 4 para aplicar a identidade visual.

### 2b. Terceiros (curados para esta stack)

> ⚠️ Plugins de terceiros têm acesso total à sua máquina e ao repo, e isso inclui a credencial do SAP. **Passe cada um no `auditor-de-skills` antes de instalar.** Só entram no `.claude/settings.json` (versionado) depois de auditados — os plugins listados abaixo **não** estão no `enabledPlugins` deste repo hoje.

> Além de instalar (`claude plugin install <nome>@<marketplace> --scope project`), o marketplace precisa estar **registrado em `extraKnownMarketplaces` no `.claude/settings.json`** para o Claude Code aceitá-lo. Ver docs para o schema exato de cada entrada.

**Trail of Bits:** segurança e Python moderno. É uma empresa de auditoria de segurança de referência.

```bash
# 1) Registre trailofbits em extraKnownMarketplaces no .claude/settings.json
# 2) Depois, no terminal (após passar cada plugin no auditor-de-skills):
claude plugin install modern-python@trailofbits --scope project
claude plugin install property-based-testing@trailofbits --scope project
claude plugin install insecure-defaults@trailofbits --scope project
claude plugin install sharp-edges@trailofbits --scope project
claude plugin install differential-review@trailofbits --scope project
claude plugin install static-analysis@trailofbits --scope project
claude plugin install supply-chain-risk-auditor@trailofbits --scope project
```
| Plugin | Para que serve aqui |
|---|---|
| `modern-python` | uv + ruff + pytest do jeito certo (bate com a stack) |
| `property-based-testing` | Hypothesis para parcelas/arredondamento. É exatamente o ponto mais crítico do domínio |
| `insecure-defaults` | Pega fail-open: CORS aberto, auth desligada em dev que vaza para prod, secrets default |
| `sharp-edges` | APIs perigosas: float em dinheiro, `verify=False` no httpx, SQL cru |
| `differential-review` | Review de segurança do diff antes de cada merge |
| `static-analysis` | Semgrep/CodeQL (pode ir para o CI também) |
| `supply-chain-risk-auditor` | Audita dependências PyPI/npm |

**wshobson/agents:** padrões de backend, banco e front.

```bash
# 1) Registre claude-code-workflows em extraKnownMarketplaces no .claude/settings.json
# 2) Depois, no terminal (após passar cada plugin no auditor-de-skills):
claude plugin install python-development@claude-code-workflows --scope project
claude plugin install backend-development@claude-code-workflows --scope project
claude plugin install database-design@claude-code-workflows --scope project
claude plugin install frontend-mobile-development@claude-code-workflows --scope project
```
| Plugin | Para que serve aqui |
|---|---|
| `python-development` | `fastapi-templates`, `async-python-patterns`, `python-testing-patterns` |
| `backend-development` | `api-design-principles`, `architecture-patterns` (hexagonal, outbox) |
| `database-design` | `postgresql-table-design` para as tabelas `contracts`/`outbox_jobs`/constraints |
| `frontend-mobile-development` | `react-state-management`, `tailwind-design-system` |

**Vercel:** performance e boas práticas de React. Roda no terminal, fora do Claude:
```bash
npx skills add vercel-labs/agent-skills --skill react-best-practices -a claude-code
npx skills add vercel-labs/agent-skills --skill web-design-guidelines -a claude-code
```

**MCP servers úteis:**
```bash
# componentes shadcn/ui direto do registry (evita componente inventado)
npx shadcn@latest mcp init --client claude
# Postgres de DEV: o Claude inspeciona schema/queries/explain. SÓ o banco local, nunca prod
claude mcp add postgres -- uvx postgres-mcp --access-mode=restricted "postgresql://dev:dev@localhost:5432/contratos"
```

### O que NÃO instalar

Pacotes gigantes de "100+ agentes" e coleções aleatórias dos sites "awesome". Eles se sobrepõem aos acima, enchem o contexto (o Claude fica mais burro, não mais esperto) e aumentam a superfície de ataque. Com 3 skills fazendo a mesma coisa, o Claude escolhe mal.

**Sobreposições que eu cortaria se o contexto pesar:**
- `code-review` × `pr-review-toolkit`: escolhe um
- `security-guidance` (hook automático) + `differential-review` (review de merge) se complementam. `security-scanning` do wshobson seria redundante, então ficou de fora

Use o `/plugin` → aba **Installed/Stats** para ver o custo de contexto e desativar o que não estiver sendo usado.

## 3. Prompt inicial

Cole o texto abaixo no Claude Code:

---

Você vai construir a Plataforma de Contratos SAP descrita em `docs/ARCHITECTURE.md`. Siga o `CLAUDE.md` à risca.

Processo:
1. Leia `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/sap/metadata.xml`, `docs/sap/payload_exemplo.json` e `docs/sap/smoke_test_referencia.py`.
2. Use o fluxo do superpowers: escreva um plano de implementação **só da Fase 0 e da Fase 1** em `docs/plans/fase-0-1.md`, com tarefas pequenas e verificáveis, e com a lista de arquivos que serão criados. **Pare e me mostre o plano antes de codar.**
3. Depois da minha aprovação, execute tarefa por tarefa com TDD (teste falhando → código → teste verde → commit).
4. Use context7 para confirmar APIs de FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic e uv. Não chute assinaturas.
   Use as skills instaladas quando se aplicarem:
   - `modern-python` e `fastapi-templates` no scaffold
   - `postgresql-table-design` nas tabelas
   - `property-based-testing` nas parcelas
   - `insecure-defaults` e `sharp-edges` ao revisar settings e client HTTP
   - `differential-review` antes de cada merge
5. Ao fechar cada fase: rode todos os checks, depois `/code-review`, corrija o que aparecer e só então me reporte o resumo e o que ficou pendente.

Fase 0 (fundação): monorepo `backend/`, `frontend/`, `infra/`, com:
- backend com uv, ruff, mypy strict, pytest, import-linter (contrato: `app.domain` não importa infra/fastapi/sqlalchemy/httpx), pydantic-settings, structlog JSON com correlation id, `/health/live` e `/health/ready`
- frontend Vite + React + TS strict + eslint + vitest (só esqueleto)
- Dockerfiles multi-stage (backend roda como `api` ou `worker` por comando), `infra/compose.dev.yml` com Postgres 16
- GitHub Actions com todos os checks e build de imagem com trivy

Fase 1 (domínio, sem I/O):
- entidades/VOs do contrato espelhando o `$metadata` (tipos, MaxLength, obrigatórios)
- validações com mensagens em português apontando o campo
- cálculo de parcelas pelo maior resto, com testes de propriedade (hypothesis): % soma 100.0000 e valor soma o total para qualquer N ≥ 1 e qualquer total
- máquina de estados da seção 4, com transições inválidas lançando erro de domínio
- mapper domínio → payload OData, validado contra `docs/sap/payload_exemplo.json` (o mapeamento do exemplo tem que gerar JSON equivalente)

Não comece a Fase 2 (SAP/worker) sem eu pedir. As decisões em aberto da seção 14 viram `TODO(decisão #N)` no código, sem valor inventado.

---

## 4. Próximas fases (prompts curtos)

- **Fase 2:** "Planeje e execute a Fase 2 do ARCHITECTURE.md: adapter SAP (CSRF, POST deep insert, classificação antes/depois do POST, parser de erro RAP), outbox com SKIP LOCKED e worker. Testes com respx e testcontainers. No fim, um comando `uv run python -m app.cli smoke --payload docs/sap/payload_exemplo.json` que eu rodo contra DEV."
- **Fase 3:** "Fase 3: API + auth Entra ID com app roles, idempotência (`Idempotency-Key` + unique `pedido_sysfertil`), 202 + GET de status, OpenAPI."
- **Fase 4:** "Fase 4: front. Use frontend-design + brfertil-design. Form de contrato com cabeçalho, itens, parceiros, parcelas calculadas (preview), textos; lista; detalhe com timeline e erros SAP mapeados aos campos. Client gerado do OpenAPI. E2E com Playwright."
- **Fase 5:** "Fase 5: métricas Prometheus, alertas, tela de reconciliação INCERTO (admin), stack Swarm prod com secrets e Traefik."
