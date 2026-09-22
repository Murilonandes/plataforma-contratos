"""
Teste do serviço ZAPI_CONTRATO_VENDAS (OData V4) — CriaContrato.

Uso:
  pip install requests
  # PowerShell
  $env:SAP_USER="usuario_tecnico"; $env:SAP_PASS="senha"
  # bash
  export SAP_USER=usuario_tecnico SAP_PASS=senha

  python teste_cria_contrato.py payload_teste.json            # só valida (dry-run)
  python teste_cria_contrato.py payload_teste.json --enviar   # valida e cria no SAP

Credenciais só via variável de ambiente. Nunca no código.
"""
import json
import os
import sys
from decimal import Decimal

import requests

BASE_URL = os.getenv(
    "SAP_BASE_URL",
    "https://s4-dev.brfertil.com.br/sap/opu/odata4/sap/zapi_contrato_vendas_o4"
    "/srvd_a2x/sap/zapi_contrato_vendas/0001/",
)
PARAMS = {"sap-client": os.getenv("SAP_CLIENT", "300"), "saml2": "disabled"}
TIMEOUT = 60

# --- regras extraídas do $metadata ---------------------------------------
MANDATORY = {
    "header": ["SalesContractType", "SalesOrganization", "DistributionChannel",
               "OrganizationDivision", "SoldToParty", "TransactionCurrency"],
    "to_Item": ["Material", "RequestedQuantity", "RequestedQuantityUnit"],
    "to_Partner": ["PartnerFunction"],
    "to_PricingElement": ["ConditionType"],
    "to_FormPag": ["Parcela", "TransactionCurrency"],
    "to_Text": ["Language", "LongTextID"],
}
MAXLEN = {
    "header": {"SalesContractType": 4, "SalesOrganization": 4, "DistributionChannel": 2,
               "OrganizationDivision": 2, "SalesOffice": 4, "SalesGroup": 3,
               "SDDocumentReason": 3, "SoldToParty": 10, "TransactionCurrency": 3,
               "IncotermsClassification": 3, "IncotermsLocation1": 70,
               "CustomerPaymentTerms": 4, "PurchaseOrderByCustomer": 35,
               "CodTaxa": 20, "StatusBlock": 2},
    "to_Item": {"SalesContractItemText": 40, "Material": 40, "RequestedQuantityUnit": 3,
                "Plant": 4, "IncotermsClassification": 3, "IncotermsLocation1": 70,
                "TransactionCurrency": 3, "CustomerPaymentTerms": 4},
    "to_Partner": {"PartnerFunction": 2, "Customer": 10, "Supplier": 10,
                   "Personnel": 8, "ContactPerson": 10},
    "to_PricingElement": {"ConditionType": 4},
    "to_FormPag": {"FormPag": 1, "TransactionCurrency": 3},
    "to_Text": {"Language": 2, "LongTextID": 4},
}
COMPUTED = {"SalesContract", "SalesContractItem", "ConditionUUID"}


def validar(p: dict) -> list[str]:
    erros = []

    def checar(obj, secao, rotulo):
        for campo in MANDATORY.get(secao, []):
            if obj.get(campo) in (None, ""):
                erros.append(f"{rotulo}: '{campo}' obrigatório")
        for campo, limite in MAXLEN.get(secao, {}).items():
            v = obj.get(campo)
            if isinstance(v, str) and len(v) > limite:
                erros.append(f"{rotulo}: '{campo}' tem {len(v)} chars (máx {limite})")
        for campo, v in obj.items():
            if v is None:
                erros.append(f"{rotulo}: '{campo}' é null — mande \"\" (Nullable=false)")
            if campo in COMPUTED:
                erros.append(f"{rotulo}: '{campo}' é gerado pelo SAP, remova")

    checar(p, "header", "Cabeçalho")
    for i, item in enumerate(p.get("to_Item", []), 1):
        checar(item, "to_Item", f"Item {i}")
        for j, c in enumerate(item.get("to_PricingElement", []), 1):
            checar(c, "to_PricingElement", f"Item {i} condição {j}")
    for sec in ("to_Partner", "to_PricingElement", "to_FormPag", "to_Text"):
        for i, obj in enumerate(p.get(sec, []), 1):
            checar(obj, sec, f"{sec}[{i}]")

    if not p.get("to_Item"):
        erros.append("Contrato sem itens")

    funcoes = [x.get("PartnerFunction") for x in p.get("to_Partner", [])]
    dup = {f for f in funcoes if funcoes.count(f) > 1}
    if dup:
        erros.append(f"Parceiro duplicado por função (chave única): {sorted(dup)}")

    parcelas = p.get("to_FormPag", [])
    if parcelas:
        soma_pct = sum(Decimal(str(x.get("Porcentagem", 0))) for x in parcelas)
        if soma_pct != Decimal("100"):
            erros.append(f"Porcentagens somam {soma_pct}, precisa fechar 100.0000")
        nums = [x.get("Parcela") for x in parcelas]
        if sorted(nums) != list(range(1, len(nums) + 1)):
            erros.append(f"Numeração de parcelas inválida: {nums}")

    return erros


def resumo_valores(p: dict):
    total_itens = Decimal("0")
    for item in p.get("to_Item", []):
        qtd = Decimal(str(item.get("RequestedQuantity", 0)))
        preco = sum(Decimal(str(c["ConditionRateValue"])) for c in item.get("to_PricingElement", []))
        total_itens += qtd * preco
    total_parcelas = sum(Decimal(str(x.get("Valor", 0))) for x in p.get("to_FormPag", []))
    print(f"Total itens (qtd × soma condições): R$ {total_itens:.2f}")
    print(f"Total parcelas:                     R$ {total_parcelas:.2f}")
    if total_parcelas and total_itens != total_parcelas:
        print("  ⚠ Valores não batem — confirme se as parcelas incluem impostos/outros componentes.")


def mostrar_erro(resp: requests.Response):
    print(f"HTTP {resp.status_code}")
    try:
        err = resp.json().get("error", {})
        print(f"  [{err.get('code')}] {err.get('message')}")
        for d in err.get("details", []):
            alvo = f" ({d.get('target')})" if d.get("target") else ""
            print(f"  - [{d.get('code')}] {d.get('message')}{alvo}")
    except ValueError:
        print(resp.text[:2000])


def enviar(p: dict):
    user, pwd = os.getenv("SAP_USER"), os.getenv("SAP_PASS")
    if not user or not pwd:
        sys.exit("Defina SAP_USER e SAP_PASS nas variáveis de ambiente.")

    s = requests.Session()
    s.auth = (user, pwd)
    s.headers.update({"Accept": "application/json"})

    # 1) CSRF token (mesma sessão/cookies no POST)
    r = s.get(BASE_URL, params=PARAMS, headers={"x-csrf-token": "Fetch"}, timeout=TIMEOUT)
    token = r.headers.get("x-csrf-token")
    if r.status_code >= 400 or not token:
        print("Falha ao obter CSRF token:")
        mostrar_erro(r)
        sys.exit(1)

    # 2) Deep insert
    try:
        r = s.post(
            BASE_URL + "CriaContrato",
            params=PARAMS,
            headers={"x-csrf-token": token, "Content-Type": "application/json"},
            data=json.dumps(p),
            timeout=TIMEOUT,
        )
    except requests.Timeout:
        print("⚠ TIMEOUT: o contrato PODE ter sido criado. NÃO reenvie antes de conferir "
              f"no SAP (VA43 / pedido cliente {p.get('PurchaseOrderByCustomer')}).")
        sys.exit(2)

    msgs = r.headers.get("sap-messages")
    if r.status_code == 201:
        body = r.json()
        print(f"✅ Contrato criado: {body.get('SalesContract')}")
        if msgs:
            print("Mensagens SAP:", json.dumps(json.loads(msgs), indent=2, ensure_ascii=False))
        with open("resposta_criacao.json", "w", encoding="utf-8") as f:
            json.dump(body, f, indent=2, ensure_ascii=False)
        print("Resposta completa salva em resposta_criacao.json")
    else:
        mostrar_erro(r)
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    with open(sys.argv[1], encoding="utf-8") as f:
        payload = json.load(f)

    erros = validar(payload)
    resumo_valores(payload)
    if erros:
        print("\n❌ Payload inválido:")
        for e in erros:
            print(f"  - {e}")
        sys.exit(1)
    print("\n✔ Payload passou nas validações do $metadata.")

    if "--enviar" in sys.argv:
        enviar(payload)
    else:
        print("Dry-run. Use --enviar para criar no SAP.")


if __name__ == "__main__":
    main()
