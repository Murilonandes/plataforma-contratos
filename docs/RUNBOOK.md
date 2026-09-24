# Runbook — Plataforma de Contratos SAP

Procedimentos de operação por situação. Cada entrada diz como a situação aparece, o que significa
e o que fazer. Estados e eventos: ARCHITECTURE §4.

## `CONFERENCIA_DIVERGENTE` → contrato em `ERRO_TECNICO`

**Como aparece:** alerta (métrica de conferência divergente + log `ERROR` do worker) e contrato em
`ERRO_TECNICO` com o evento `CONFERENCIA_DIVERGENTE` na timeline. O `detalhe` do evento indica a
parcela e o campo que divergiram.

**O que significa:** antes de enviar, o worker recalculou as parcelas do snapshot congelado na
submissão (mesma versão do algoritmo) e o resultado não bateu com o que foi congelado. **Nada foi
enviado ao SAP**: a conferência roda antes do CSRF e do marcador `request_sent_at`. Com a mesma
versão do algoritmo, divergência indica **bug no cálculo de parcelas** ou snapshot corrompido.

**O que fazer:**
1. **Não usar `LIBERAR_REENVIO`.** Ele reenvia o **mesmo** snapshot, a conferência diverge de novo e
   o contrato volta para `ERRO_TECNICO`.
2. Abrir incidente de bug com o `contract_id`, o `snapshot_id` e o `detalhe` do evento. O time
   compara o snapshot com o recálculo e corrige o cálculo (ou o dado corrompido).
3. **Cancelar** o contrato (`CANCELAR`, admin, com justificativa citando o incidente).
4. **Resubmeter** como contrato novo depois da correção. A submissão congela um snapshot novo.

**Não confundir com:** versão do algoritmo diferente da atual (snapshot congelado antes de um
deploy que trocou o algoritmo). Nesse caso o worker **envia sem conferir**, registra
`conferencia=pulada_versao` no `detalhe` do `WORKER_PEGOU` e emite log `WARNING`, sem alerta nem
`ERRO_TECNICO`.
