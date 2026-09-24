"""Modelo relacional (SQLAlchemy Core) do schema ``contratos`` — ARCHITECTURE §6.

E o ``target_metadata`` do Alembic: ``alembic check`` falha se a migracao e este
modelo divergirem. Regras do schema (Tarefa 2.3):

- tudo no schema ``contratos`` (nada no ``public``);
- estado/evento/ator como ``TEXT`` + ``CHECK`` com os valores dos enums do dominio
  (nunca ``ENUM`` do Postgres: evolui sem ``ALTER TYPE``);
- todo instante e ``TIMESTAMPTZ``, sem ``DEFAULT now()``: o valor vem do ``Clock``
  da aplicacao (D5);
- dinheiro/quantidade consultavel e ``NUMERIC`` (``contract_snapshots.total``);
  o contrato completo fica no snapshot em JSONB, com decimal como string de escala fixa;
- ``pedido_sysfertil`` vazio e ``NULL`` (``CHECK <> ''``) e e unico entre contratos
  ativos (indice parcial fora de ``ERRO_NEGOCIO``/``CANCELADO``);
- ``outbox_jobs.lock_token`` para fencing (D4); indice parcial so em job pendente,
  sem ``now()`` no predicado (o Postgres exige funcao ``IMMUTABLE``);
- ``contract_submissions.request_body`` em ``BYTEA`` com os bytes exatos e ``CHECK``
  de que ``request_sha256`` e o sha256 deles (D7);
- imutabilidade por trigger (na migracao): eventos e snapshots so ``INSERT``;
  submissao aceita um unico ``UPDATE`` (a resposta) e nunca ``DELETE``.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    SmallInteger,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from app.domain.enums import ActorKind, ContractStatus, TransitionEvent

SCHEMA: Final = "contratos"

metadata = MetaData(
    schema=SCHEMA,
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    },
)

TZ = TIMESTAMP(timezone=True)


def valores_sql(valores: object) -> str:
    """``'A', 'B'`` a partir de um enum (ordem de declaracao, estavel)."""
    return ", ".join(f"'{v.value}'" for v in valores)  # type: ignore[attr-defined]


_ESTADOS = valores_sql(ContractStatus)
_EVENTOS = valores_sql(TransitionEvent)
_ATORES = valores_sql(ActorKind)

contracts = Table(
    "contracts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("status", Text, nullable=False),
    Column("origin", Text, nullable=False),
    Column("created_by", Text, nullable=False),
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column("pedido_sysfertil", Text, nullable=True),
    Column("entrada", JSONB, nullable=False),
    Column("sap_contract_number", Text, nullable=True),
    Column("version", Integer, nullable=False),
    Column("created_at", TZ, nullable=False),
    Column("updated_at", TZ, nullable=False),
    CheckConstraint(f"status IN ({_ESTADOS})", name="status"),
    CheckConstraint("origin IN ('WEB', 'API')", name="origin"),
    CheckConstraint("pedido_sysfertil <> ''", name="pedido_sysfertil_nao_vazio"),
    CheckConstraint("sap_contract_number ~ '^[0-9]{10}$'", name="sap_contract_number_vbeln"),
    CheckConstraint("version >= 1", name="version"),
    Index(
        "uq_contracts_pedido_sysfertil_ativo",
        "pedido_sysfertil",
        unique=True,
        postgresql_where=text(
            "pedido_sysfertil IS NOT NULL AND status NOT IN ('ERRO_NEGOCIO', 'CANCELADO')"
        ),
    ),
)

contract_snapshots = Table(
    "contract_snapshots",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("contract_id", UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False),
    Column("snapshot", JSONB, nullable=False),
    Column("algoritmo_parcelas", Text, nullable=False),
    Column("entrada_parcelas", JSONB, nullable=False),
    Column("total", Numeric(15, 2), nullable=False),
    Column("created_at", TZ, nullable=False),
    CheckConstraint("algoritmo_parcelas <> ''", name="algoritmo_parcelas"),
    CheckConstraint("total > 0", name="total"),
    Index("ix_contract_snapshots_contract_id", "contract_id"),
)

contract_events = Table(
    "contract_events",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("contract_id", UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False),
    Column("de", Text, nullable=False),
    Column("para", Text, nullable=False),
    Column("evento", Text, nullable=False),
    Column("ator_kind", Text, nullable=False),
    Column("ator_identifier", Text, nullable=False),
    Column("justificativa", Text, nullable=True),
    Column("sap_contract_number", Text, nullable=True),
    Column("detalhe", JSONB, nullable=False),
    Column("occurred_at", TZ, nullable=False),
    CheckConstraint(f"de IN ({_ESTADOS})", name="de"),
    CheckConstraint(f"para IN ({_ESTADOS})", name="para"),
    CheckConstraint(f"evento IN ({_EVENTOS})", name="evento"),
    CheckConstraint(f"ator_kind IN ({_ATORES})", name="ator_kind"),
    CheckConstraint("ator_identifier <> ''", name="ator_identifier"),
    Index("ix_contract_events_contract_id", "contract_id", "id"),
)

outbox_jobs = Table(
    "outbox_jobs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("seq", BigInteger, Identity(always=True), nullable=False, unique=True),
    Column("contract_id", UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False),
    Column("snapshot_id", UUID(as_uuid=True), ForeignKey("contract_snapshots.id"), nullable=False),
    Column("kind", Text, nullable=False),
    Column("run_after", TZ, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("lock_token", UUID(as_uuid=True), nullable=True),
    Column("locked_until", TZ, nullable=True),
    Column("concluido_em", TZ, nullable=True),
    Column("last_error", Text, nullable=True),
    Column("correlation_id", Text, nullable=False),
    CheckConstraint("kind IN ('CREATE')", name="kind"),
    CheckConstraint("attempts >= 0", name="attempts"),
    CheckConstraint("(lock_token IS NULL) = (locked_until IS NULL)", name="lock_par"),
    CheckConstraint("concluido_em IS NULL OR lock_token IS NULL", name="concluido_sem_lock"),
    Index(
        "ix_outbox_jobs_pendentes",
        "run_after",
        "seq",
        postgresql_where=text("concluido_em IS NULL"),
    ),
)

contract_submissions = Table(
    "contract_submissions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("job_id", UUID(as_uuid=True), ForeignKey("outbox_jobs.id"), nullable=False),
    Column("contract_id", UUID(as_uuid=True), ForeignKey("contracts.id"), nullable=False),
    Column("snapshot_id", UUID(as_uuid=True), ForeignKey("contract_snapshots.id"), nullable=False),
    Column("tentativa", Integer, nullable=False),
    Column("request_sent_at", TZ, nullable=False),
    Column("request_body", LargeBinary, nullable=False),
    Column("request_sha256", Text, nullable=False),
    Column("request_json", JSONB, nullable=True),
    Column("response_status", Integer, nullable=True),
    Column("response_body", LargeBinary, nullable=True),
    Column("response_truncado", Boolean, nullable=True),
    Column("response_json", JSONB, nullable=True),
    Column("sap_messages", JSONB, nullable=True),
    Column("duration_ms", Integer, nullable=True),
    Column("error_class", Text, nullable=True),
    Column("respondido", Boolean, nullable=False),
    CheckConstraint("tentativa >= 1", name="tentativa"),
    CheckConstraint("request_sha256 = encode(sha256(request_body), 'hex')", name="request_sha256"),
    CheckConstraint(
        "response_body IS NULL OR octet_length(response_body) <= 1048576", name="response_1mib"
    ),
    CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_ms"),
    Index("uq_contract_submissions_job_tentativa", "job_id", "tentativa", unique=True),
)

sap_heartbeat = Table(
    "sap_heartbeat",
    metadata,
    Column("id", SmallInteger, primary_key=True),
    Column("ultimo_csrf_ok", TZ, nullable=True),
    CheckConstraint("id = 1", name="linha_unica"),
)
