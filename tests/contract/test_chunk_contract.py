"""Teste de contrato: todo trecho indexado tem os metadados obrigatórios.

Pega, no começo, o erro que só apareceria depois como "o filtro não funciona".
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.pii import contem_dado_pessoal
from src.schema import Chunk, Natureza

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "chunks_exemplo.json"
OBRIGATORIOS = {"source_file", "doc_type", "chunk_id", "sensitivity", "base_id"}


@pytest.fixture(scope="module")
def chunks_brutos() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_todo_chunk_tem_metadado_obrigatorio(chunks_brutos):
    for bruto in chunks_brutos:
        faltando = OBRIGATORIOS - set(bruto["metadata"])
        assert not faltando, f"{bruto['metadata'].get('chunk_id')} sem: {faltando}"


def test_todo_chunk_e_valido_no_schema(chunks_brutos):
    for bruto in chunks_brutos:
        Chunk.model_validate(bruto)


def test_chunk_sem_metadado_obrigatorio_e_rejeitado(chunks_brutos):
    incompleto = json.loads(json.dumps(chunks_brutos[0]))
    del incompleto["metadata"]["sensitivity"]
    with pytest.raises(ValidationError):
        Chunk.model_validate(incompleto)


def test_chunk_ids_sao_unicos(chunks_brutos):
    ids = [c["metadata"]["chunk_id"] for c in chunks_brutos]
    assert len(ids) == len(set(ids))


def test_as_quatro_naturezas_estao_representadas(chunks_brutos):
    naturezas = {c["metadata"]["natureza"] for c in chunks_brutos}
    assert naturezas == {n.value for n in Natureza}


def test_nenhum_chunk_traz_dado_pessoal_nao_mascarado(chunks_brutos):
    """O texto indexado já deve vir mascarado; a marca contem_dado_pessoal registra que houve máscara."""
    for bruto in chunks_brutos:
        assert not contem_dado_pessoal(bruto["texto"]), bruto["metadata"]["chunk_id"]


def test_chunk_sintetico_e_marcado_como_sintetico(chunks_brutos):
    sinteticos = [c for c in chunks_brutos if c["metadata"]["base_id"].endswith("conversacional")]
    assert sinteticos
    assert all(c["metadata"].get("sintetico") is True for c in sinteticos)
