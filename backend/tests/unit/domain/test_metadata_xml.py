"""``docs/sap/metadata.xml`` e XML bem-formado, sem pre-processamento (Tarefa 2.0).

O arquivo ja teve ``&`` sem escape nas anotacoes ``DocumentationRef`` e o teste
do dominio precisava escapar antes de parsear. Agora a fonte esta correta e o
parse e direto: se alguem colar um ``$metadata`` sem escape de novo, quebra aqui.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from tests.unit.domain._referencias_sap import caminho_metadata

_EDM = "{http://docs.oasis-open.org/odata/ns/edm}"
_ENTIDADE_OU_AMP_SOLTO = re.compile(rb"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")


def test_metadata_parseia_sem_pre_processamento() -> None:
    raiz = ET.parse(caminho_metadata()).getroot()  # noqa: S314 — arquivo do proprio repo
    tipos = {et.get("Name") for et in raiz.iter(f"{_EDM}EntityType")}
    assert {"CriaContratoType", "ItensContratoType", "ParcelasContratoType"} <= tipos


def test_metadata_nao_tem_e_comercial_sem_escape() -> None:
    assert _ENTIDADE_OU_AMP_SOLTO.search(caminho_metadata().read_bytes()) is None


def test_document_ref_mantem_o_valor_original_depois_do_parse() -> None:
    raiz = ET.parse(caminho_metadata()).getroot()  # noqa: S314 — arquivo do proprio repo
    refs = {
        a.get("String")
        for a in raiz.iter(f"{_EDM}Annotation")
        if a.get("Term") == "SAP__common.DocumentationRef"
    }
    assert "urn:sap-com:documentation:key?=type=DE&id=VBELN" in refs
