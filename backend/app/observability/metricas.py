"""Adapter da porta ``Metrics`` na Fase 2 (D15): uma linha de log JSON ``ERROR``.

O campo fixo ``alert`` leva o nome do alerta; o Zabbix alerta por padrao de log.
Na Fase 5 entra o adapter Prometheus atras da mesma porta. Labels: so ids e
nomes de evento (nunca corpo, mensagem do SAP ou dado pessoal).
"""

from __future__ import annotations

from typing import Final

import structlog

ALERTAS: Final = frozenset(
    {"conferencia_divergente", "contrato_incerto", "erro_tecnico", "sap_heartbeat_atrasado"}
)

_log = structlog.get_logger("app.alerta")


class MetricasEmLog:
    def incrementar(self, evento: str, **labels: str | int) -> None:
        if evento not in ALERTAS:
            raise ValueError(f"alerta desconhecido: {evento}")
        _log.error("alerta", alert=evento, **labels)
