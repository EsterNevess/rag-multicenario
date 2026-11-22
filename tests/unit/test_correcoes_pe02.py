"""Correções achadas na rodada do PE-02: citação com colchetes largos e busca por trecho com várias alternativas ("A|B")."""

from __future__ import annotations

import sqlite3

import pytest

from src.capacidades import capacidades_do_cenario
from src.generate import normalizar_citacoes
from src.normalize import COLUNAS_CONTRATACAO, _ddl
from src.router import consultar
from src.schema import Natureza, PlanoConsulta, Rota

# ----------------------------------------------------------------- citação 【1】 do modelo


@pytest.mark.parametrize("entrada, esperado", [
    ("São 3 registros【1】.", "São 3 registros[1]."),
    ("Veja【1, 2】 e【3】.", "Veja[1, 2] e[3]."),
    ("Texto【1†L10-L12】.", "Texto[1]."),  # o formato com marca de fonte e linhas
    ("Texto［2］.", "Texto[2]."),  # colchete largo ［ ］
    ("Já está certo [1].", "Já está certo [1]."),
    ("Sem citação nenhuma.", "Sem citação nenhuma."),
    ("Colchete de outra coisa 【ver anexo】.", "Colchete de outra coisa 【ver anexo】."),  # sem número: não é citação
])
def test_normalizar_citacoes(entrada, esperado):
    assert normalizar_citacoes(entrada) == esperado


# ----------------------------------------------------------------- "A|B" na busca por trecho


@pytest.fixture()
def banco(tmp_path):
    caminho = tmp_path / "v1.sqlite"
    c = sqlite3.connect(caminho)
    c.execute(_ddl("contratacao", COLUNAS_CONTRATACAO))
    for n, objeto in enumerate(["Material de escritório", "Serviço de limpeza", "Obra de drenagem", "Material de limpeza"], 1):
        linha = dict.fromkeys(COLUNAS_CONTRATACAO)
        linha.update(numero_controle_pncp=f"00000000000001-1-{n:06d}/2026", modalidade_nome="Dispensa", valor_total_estimado=10.0 * n,
                     orgao_razao_social="Prefeitura A", objeto_compra=objeto, data_publicacao="2026-01-10", uf_sigla="RJ")
        c.execute(f"INSERT INTO contratacao VALUES ({','.join('?' * len(COLUNAS_CONTRATACAO))})", [linha[k] for k in COLUNAS_CONTRATACAO])
    c.commit()
    c.close()
    return caminho


def _consulta(banco, valor):
    plano = PlanoConsulta(rota=Rota.ESTRUTURADA, naturezas=[Natureza.ESTRUTURADA], entidade="contratacao",
                          filtros={"objeto_compra__contem": valor}, operacoes=["listar"])
    return consultar(plano, banco, capacidades_do_cenario("rj"))


def test_alternativas_separadas_por_barra_valem_como_ou(banco):
    r = _consulta(banco, "escritorio|drenagem")
    assert sorted(l["objeto_compra"] for l in r.linhas) == ["Material de escritório", "Obra de drenagem"]
    assert "escritorio ou drenagem" in r.descricao


def test_uma_so_palavra_continua_igual(banco):
    assert len(_consulta(banco, "limpeza").linhas) == 2


def test_barra_com_espacos_e_alternativa_vazia(banco):
    assert len(_consulta(banco, " escritorio | ").linhas) == 1  # a alternativa vazia some, e o resto vale
    assert len(_consulta(banco, "escritorio|").linhas) == 1


def test_o_texto_da_alternativa_continua_escapado(banco):
    assert _consulta(banco, "100%|_").linhas == []  # % e _ são texto, não curinga
