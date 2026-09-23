"""VOs do contrato (``@dataclass(frozen=True, slots=True)``, sem pydantic).

Validacao ACUMULADA: ``Contract.criar(dados)`` valida o contrato inteiro com um
unico ``ErrorCollector`` e levanta ``DomainValidationError`` UMA vez, no fim.
As regras inter-campo (``rules.py``) entram no mesmo coletor, depois da
validacao campo a campo. Os dataclasses so sao construidos depois, com dados
ja validos. O
``__post_init__`` so confere tipos (protege uso programatico; nunca e o
caminho de validacao de entrada do usuario).

Entrada: ``Mapping`` no formato do payload OData (PascalCase, navegacao
``to_*``), ja tipada: ``Decimal`` (dinheiro/quantidade), ``datetime.date``
(Edm.Date) e ``int`` (Edm.Int32). Converter JSON para esses tipos e papel da
API. Campo fora da especificacao e rejeitado (``unknown_field``): o cliente nao
envia ``StatusBlock`` nem campos ``Computed``.

Normalizacao (explicita, e so isto):
- strip em toda string;
- uppercase so em campo anotado ``SAP__common.IsUpperCase`` no ``$metadata``.
  Das 9 anotacoes, 8 sao do ``SalesContract`` (Computed): sobra ``FormPag``.
Nada mais e transformado. Decimal com escala acima da permitida e erro
(``decimal_scale``), nunca arredondamento; o ``money.py`` so canoniza o
expoente de um valor ja valido (``1`` -> ``1.000``).

MaxLength e validado depois do strip. Obrigatoriedade = ``FieldControl/Mandatory``
do ``$metadata`` (``Nullable=false`` nao implica obrigatorio). A tabela
``ESPECIFICACOES`` espelha o ``$metadata``; ``test_contract_metadata`` confere.
Strings sem MaxLength no metadata tem teto provisorio (``TODO(decisao #5)``):
255 em ``NotaInternaCli``, ``PedidoSysFertil`` e ``Culture``; 1000 em ``LongText``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Final

from app.domain.errors import ErrorCode, ErrorCollector, campo, indice
from app.domain.money import quantize_brl, quantize_pct, quantize_qty, quantize_rate
from app.domain.rules import validar_regras


class Tipo(Enum):
    TEXTO = "texto"
    DECIMAL = "decimal"
    DATA = "data"
    INTEIRO = "inteiro"


@dataclass(frozen=True, slots=True)
class Campo:
    """Especificacao de um campo, espelho do ``$metadata``."""

    odata: str
    attr: str
    tipo: Tipo
    obrigatorio: bool = False  # FieldControl=Mandatory
    max_len: int | None = None  # MaxLength (so texto)
    maiusculo: bool = False  # IsUpperCase
    escala: int | None = None  # Scale (so decimal)
    precisao: int | None = None  # Precision (so decimal)
    positivo: bool = False  # regra do dominio (quantidade, numero da parcela)


def _t(odata: str, attr: str, max_len: int | None, *, obrigatorio: bool = False) -> Campo:
    return Campo(odata, attr, Tipo.TEXTO, obrigatorio=obrigatorio, max_len=max_len)


_T = Tipo

# Strings sem MaxLength no metadata: teto provisorio ate a Sysfertil confirmar
# o tamanho real (TODO(decisao #5)). LongText tem o seu (1000).
_TETO_PROVISORIO: Final = 255

CABECALHO: Final = (
    _t("SalesContractType", "sales_contract_type", 4, obrigatorio=True),
    _t("SalesOrganization", "sales_organization", 4, obrigatorio=True),
    _t("DistributionChannel", "distribution_channel", 2, obrigatorio=True),
    _t("OrganizationDivision", "organization_division", 2, obrigatorio=True),
    _t("SalesOffice", "sales_office", 4),
    _t("SalesGroup", "sales_group", 3),
    _t("SDDocumentReason", "sd_document_reason", 3),
    _t("SoldToParty", "sold_to_party", 10, obrigatorio=True),
    _t("TransactionCurrency", "transaction_currency", 3, obrigatorio=True),
    _t("IncotermsClassification", "incoterms_classification", 3),
    _t("IncotermsLocation1", "incoterms_location1", 70),
    _t("CustomerPaymentTerms", "customer_payment_terms", 4),
    _t("PurchaseOrderByCustomer", "purchase_order_by_customer", 35),
    Campo("CustomerPurchaseOrderDate", "customer_purchase_order_date", _T.DATA),
    Campo("SalesContractValidityEndDate", "sales_contract_validity_end_date", _T.DATA),
    _t("NotaInternaCli", "nota_interna_cli", _TETO_PROVISORIO),  # TODO(decisao #5) MaxLength real
    _t("PedidoSysFertil", "pedido_sysfertil", _TETO_PROVISORIO),  # TODO(decisao #5) MaxLength real
    _t("CodTaxa", "cod_taxa", 20),  # TODO(decisao #7) opcional
)

ITEM: Final = (
    _t("SalesContractItemText", "sales_contract_item_text", 40),
    _t("Material", "material", 40, obrigatorio=True),
    Campo(
        "RequestedQuantity",
        "requested_quantity",
        _T.DECIMAL,
        obrigatorio=True,
        escala=3,
        precisao=15,
        positivo=True,
    ),
    _t("RequestedQuantityUnit", "requested_quantity_unit", 3, obrigatorio=True),
    _t("Plant", "plant", 4),
    _t("IncotermsClassification", "incoterms_classification", 3),
    _t("IncotermsLocation1", "incoterms_location1", 70),
    _t("TransactionCurrency", "transaction_currency", 3),
    _t("Culture", "culture", _TETO_PROVISORIO),  # TODO(decisao #5) MaxLength real
    Campo("ScheduleDate", "schedule_date", _T.DATA),
    Campo("ScheduleDate2", "schedule_date2", _T.DATA),  # TODO(decisao #6) semantica
    _t("CustomerPaymentTerms", "customer_payment_terms", 4),
)

PRECO: Final = (
    _t("ConditionType", "condition_type", 4, obrigatorio=True),
    # TODO(decisao #4) numero x string na serializacao (Fase 2)
    Campo("ConditionRateValue", "condition_rate_value", _T.DECIMAL, escala=9, precisao=23),
)

PARCEIRO: Final = (
    _t("PartnerFunction", "partner_function", 2, obrigatorio=True),
    _t("Customer", "customer", 10),
    _t("Supplier", "supplier", 10),
    _t("Personnel", "personnel", 8),
    _t("ContactPerson", "contact_person", 10),
)

PARCELA: Final = (
    Campo("Parcela", "parcela", _T.INTEIRO, obrigatorio=True, positivo=True),
    _t("TransactionCurrency", "transaction_currency", 3, obrigatorio=True),
    Campo("Porcentagem", "porcentagem", _T.DECIMAL, escala=4, precisao=15),
    # Scale "variable" (moeda): BRL = 2 casas
    Campo("Valor", "valor", _T.DECIMAL, escala=2, precisao=15),
    Campo("Data", "data", _T.DATA),  # data base (ZFBDT), nao vencimento
    Campo("FormPag", "form_pag", _T.TEXTO, max_len=1, maiusculo=True),
)

TEXTO: Final = (
    _t("Language", "language", 2, obrigatorio=True),
    _t("LongTextID", "long_text_id", 4, obrigatorio=True),
    _t("LongText", "long_text", 1000),  # TODO(decisao #5) placeholder generoso
)

ESPECIFICACOES: Final[Mapping[str, tuple[Campo, ...]]] = {
    "CriaContratoType": CABECALHO,
    "ItensContratoType": ITEM,
    "PrecosItemType": PRECO,
    "PrecosCabecalhoType": PRECO,
    "ParceirosContratoType": PARCEIRO,
    "ParcelasContratoType": PARCELA,
    "TextosContratoType": TEXTO,
}

_QUANTIZADOR: Final[Mapping[int, Callable[[Decimal], Decimal]]] = {
    2: quantize_brl,
    3: quantize_qty,
    4: quantize_pct,
    9: quantize_rate,
}

_IDENTIFICADORES_PARCEIRO: Final = ("customer", "supplier", "personnel", "contact_person")


# ---- Validacao de campo ------------------------------------------------------


def _casas_decimais(v: Decimal) -> int:
    """Casas decimais significativas, sem ``normalize()`` (que arredonda no contexto)."""
    _, digitos, expoente = v.as_tuple()
    exp = int(expoente)
    d = list(digitos)
    while exp < 0 and len(d) > 1 and d[-1] == 0:
        d.pop()
        exp += 1
    return max(0, -exp)


def _digitos_inteiros(v: Decimal) -> int:
    return v.adjusted() + 1 if v else 0


def _validar_texto(c: Campo, bruto: object, path: str, col: ErrorCollector) -> str:
    if bruto is None:
        valor = ""
    elif isinstance(bruto, str):
        valor = bruto.strip()
    else:
        col.adicionar(path, ErrorCode.INVALID_TYPE, tipo="texto")
        return ""
    if c.maiusculo:
        valor = valor.upper()
    if c.obrigatorio and not valor:
        col.adicionar(path, ErrorCode.REQUIRED)
    elif c.max_len is not None and len(valor) > c.max_len:
        col.adicionar(path, ErrorCode.MAX_LENGTH, max=c.max_len)
    return valor


def _validar_decimal(c: Campo, bruto: object, path: str, col: ErrorCollector) -> Decimal | None:
    if bruto is None:
        if c.obrigatorio:
            col.adicionar(path, ErrorCode.REQUIRED)
        return None
    if not isinstance(bruto, Decimal):
        col.adicionar(path, ErrorCode.NOT_DECIMAL)
        return None
    if not bruto.is_finite():
        col.adicionar(path, ErrorCode.NOT_FINITE)
        return None
    if c.escala is None or c.precisao is None:  # pragma: no cover - garantido pela tabela
        raise TypeError(f"campo decimal {c.odata} sem escala/precisao")
    if _casas_decimais(bruto) > c.escala:
        col.adicionar(path, ErrorCode.DECIMAL_SCALE, max=c.escala)
        return None
    max_inteiros = c.precisao - c.escala
    if _digitos_inteiros(bruto) > max_inteiros:
        col.adicionar(path, ErrorCode.DECIMAL_PRECISION, max=max_inteiros)
        return None
    if c.positivo and bruto <= 0:
        col.adicionar(path, ErrorCode.MUST_BE_POSITIVE)
        return None
    return _QUANTIZADOR[c.escala](bruto)  # so canoniza o expoente: valor ja cabe na escala


def _validar_data(c: Campo, bruto: object, path: str, col: ErrorCollector) -> date | None:
    if bruto is None:
        if c.obrigatorio:  # pragma: no cover - nenhuma data e Mandatory no metadata
            col.adicionar(path, ErrorCode.REQUIRED)
        return None
    if type(bruto) is not date:  # datetime e subclasse de date: rejeitado
        col.adicionar(path, ErrorCode.INVALID_TYPE, tipo="data")
        return None
    return bruto


def _validar_inteiro(c: Campo, bruto: object, path: str, col: ErrorCollector) -> int | None:
    if bruto is None:
        if c.obrigatorio:
            col.adicionar(path, ErrorCode.REQUIRED)
        return None
    if type(bruto) is not int:  # bool e subclasse de int: rejeitado
        col.adicionar(path, ErrorCode.INVALID_TYPE, tipo="inteiro")
        return None
    if c.positivo and bruto <= 0:
        col.adicionar(path, ErrorCode.MUST_BE_POSITIVE)
        return None
    return bruto


_VALIDADORES: Final[Mapping[Tipo, Callable[[Campo, object, str, ErrorCollector], object]]] = {
    Tipo.TEXTO: _validar_texto,
    Tipo.DECIMAL: _validar_decimal,
    Tipo.DATA: _validar_data,
    Tipo.INTEIRO: _validar_inteiro,
}


def validar_campo(c: Campo, bruto: object, path: str, col: ErrorCollector) -> object:
    """Valida um valor avulso contra uma especificacao (ex.: ``total`` das parcelas)."""
    return _VALIDADORES[c.tipo](c, bruto, path, col)


def _validar_entidade(
    dados: Mapping[str, object],
    specs: tuple[Campo, ...],
    col: ErrorCollector,
    path: str,
    navegacoes: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Valida os campos de uma entidade; campo fora da especificacao e erro."""
    conhecidos = {c.odata for c in specs} | navegacoes
    for chave in dados:
        if chave not in conhecidos:
            col.adicionar(campo(path, str(chave)), ErrorCode.UNKNOWN_FIELD)
    return {
        c.attr: _VALIDADORES[c.tipo](c, dados.get(c.odata), campo(path, c.odata), col)
        for c in specs
    }


def _elementos(
    dados: Mapping[str, object], navegacao: str, col: ErrorCollector, path: str
) -> list[tuple[str, Mapping[str, object]]]:
    """(path, elemento) de uma lista de navegacao; ausente/None = lista vazia."""
    bruto = dados.get(navegacao)
    p = campo(path, navegacao)
    if bruto is None:
        return []
    if not isinstance(bruto, list | tuple):
        col.adicionar(p, ErrorCode.INVALID_TYPE, tipo="lista")
        return []
    resultado: list[tuple[str, Mapping[str, object]]] = []
    for i, elemento in enumerate(bruto):
        pe = indice(p, i)
        if not isinstance(elemento, Mapping):
            col.adicionar(pe, ErrorCode.INVALID_TYPE, tipo="objeto")
            continue
        resultado.append((pe, elemento))
    return resultado


def _recebidos(dados: Mapping[str, object], navegacao: str) -> int | None:
    """Quantos elementos vieram (validos ou nao); ``None`` se nem e lista."""
    bruto = dados.get(navegacao)
    if bruto is None:
        return 0
    if isinstance(bruto, list | tuple):
        return len(bruto)
    return None


# ---- Invariantes de tipo (uso programatico)----------------------------------


def _tipo_ok(c: Campo, valor: object) -> bool:
    if c.tipo is Tipo.TEXTO:
        return isinstance(valor, str)
    if c.tipo is Tipo.DECIMAL:
        return isinstance(valor, Decimal) or (valor is None and not c.obrigatorio)
    if c.tipo is Tipo.DATA:
        return valor is None or (isinstance(valor, date) and not isinstance(valor, datetime))
    return (type(valor) is int) or (valor is None and not c.obrigatorio)


def _checar_tipos(obj: object, specs: tuple[Campo, ...]) -> None:
    for c in specs:
        valor = getattr(obj, c.attr)
        if not _tipo_ok(c, valor):
            raise TypeError(
                f"{type(obj).__name__}.{c.attr}: tipo invalido ({type(valor).__name__})"
            )


def _checar_tupla(obj: object, attr: str, cls: type) -> None:
    valor = getattr(obj, attr)
    if not isinstance(valor, tuple) or not all(isinstance(v, cls) for v in valor):
        raise TypeError(
            f"{type(obj).__name__}.{attr}: tipo invalido (esperado tuple[{cls.__name__}])"
        )


# ---- VOs ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Header:
    sales_contract_type: str
    sales_organization: str
    distribution_channel: str
    organization_division: str
    sales_office: str
    sales_group: str
    sd_document_reason: str
    sold_to_party: str
    transaction_currency: str
    incoterms_classification: str
    incoterms_location1: str
    customer_payment_terms: str
    purchase_order_by_customer: str
    customer_purchase_order_date: date | None
    sales_contract_validity_end_date: date | None
    nota_interna_cli: str
    pedido_sysfertil: str
    cod_taxa: str

    def __post_init__(self) -> None:
        _checar_tipos(self, CABECALHO)


@dataclass(frozen=True, slots=True)
class PricingElement:
    condition_type: str
    condition_rate_value: Decimal | None

    def __post_init__(self) -> None:
        _checar_tipos(self, PRECO)


@dataclass(frozen=True, slots=True)
class Item:
    sales_contract_item_text: str
    material: str
    requested_quantity: Decimal
    requested_quantity_unit: str
    plant: str
    incoterms_classification: str
    incoterms_location1: str
    transaction_currency: str
    culture: str
    schedule_date: date | None
    schedule_date2: date | None
    customer_payment_terms: str
    pricing: tuple[PricingElement, ...]

    def __post_init__(self) -> None:
        _checar_tipos(self, ITEM)
        _checar_tupla(self, "pricing", PricingElement)


@dataclass(frozen=True, slots=True)
class Partner:
    partner_function: str
    customer: str
    supplier: str
    personnel: str
    contact_person: str

    def __post_init__(self) -> None:
        _checar_tipos(self, PARCEIRO)


@dataclass(frozen=True, slots=True)
class Installment:
    parcela: int
    transaction_currency: str
    porcentagem: Decimal | None
    valor: Decimal | None
    data: date | None
    form_pag: str

    def __post_init__(self) -> None:
        _checar_tipos(self, PARCELA)


@dataclass(frozen=True, slots=True)
class Text:
    language: str
    long_text_id: str
    long_text: str

    def __post_init__(self) -> None:
        _checar_tipos(self, TEXTO)


@dataclass(frozen=True, slots=True)
class Contract:
    header: Header
    items: tuple[Item, ...]
    partners: tuple[Partner, ...]
    pricing: tuple[PricingElement, ...]
    installments: tuple[Installment, ...]
    texts: tuple[Text, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.header, Header):
            raise TypeError("Contract.header: tipo invalido (esperado Header)")
        for attr, cls in (
            ("items", Item),
            ("partners", Partner),
            ("pricing", PricingElement),
            ("installments", Installment),
            ("texts", Text),
        ):
            _checar_tupla(self, attr, cls)

    @classmethod
    def criar(cls, dados: Mapping[str, object]) -> Contract:
        """Valida o contrato inteiro, levanta uma vez com todos os erros, e so entao constroi."""
        if not isinstance(dados, Mapping):
            raise TypeError("Contract.criar espera um Mapping (payload do contrato)")
        col = ErrorCollector()
        navs = frozenset({"to_Item", "to_Partner", "to_FormPag", "to_PricingElement", "to_Text"})
        header = _validar_entidade(dados, CABECALHO, col, "", navs)

        itens: list[dict[str, Any]] = []
        for path, el in _elementos(dados, "to_Item", col, ""):
            item = _validar_entidade(el, ITEM, col, path, frozenset({"to_PricingElement"}))
            item["pricing"] = [
                _validar_entidade(p, PRECO, col, pp)
                for pp, p in _elementos(el, "to_PricingElement", col, path)
            ]
            itens.append(item)

        parceiros: list[dict[str, Any]] = []
        funcoes: list[tuple[str, str]] = []
        for path, el in _elementos(dados, "to_Partner", col, ""):
            parceiro = _validar_entidade(el, PARCEIRO, col, path)
            if not any(parceiro[k] for k in _IDENTIFICADORES_PARCEIRO):
                col.adicionar(path, ErrorCode.PARTNER_IDENTIFIER_REQUIRED)
            parceiros.append(parceiro)
            funcoes.append((path, parceiro["partner_function"]))

        precos = [
            _validar_entidade(el, PRECO, col, p)
            for p, el in _elementos(dados, "to_PricingElement", col, "")
        ]
        parcelas = [
            _validar_entidade(el, PARCELA, col, p)
            for p, el in _elementos(dados, "to_FormPag", col, "")
        ]
        textos = [
            _validar_entidade(el, TEXTO, col, p) for p, el in _elementos(dados, "to_Text", col, "")
        ]

        col.incorporar(
            validar_regras(parceiros=funcoes, itens_recebidos=_recebidos(dados, "to_Item")),
            prefixo="",
        )
        col.levantar_se_houver()

        return cls(
            header=Header(**header),
            items=tuple(
                Item(**{**i, "pricing": tuple(PricingElement(**p) for p in i["pricing"])})
                for i in itens
            ),
            partners=tuple(Partner(**p) for p in parceiros),
            pricing=tuple(PricingElement(**p) for p in precos),
            installments=tuple(Installment(**p) for p in parcelas),
            texts=tuple(Text(**t) for t in textos),
        )
