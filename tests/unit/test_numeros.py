"""Conferência dos números da resposta contra a evidência."""

from __future__ import annotations

import json

import pytest

from src.generate import formatar, responder
from src.numeros import extrair, interpretacoes, numeros_nao_conferidos
from tests.fakes import LLMRoteirizado
from tests.unit.test_generate import _ctx, indice  # noqa: F401  (a fixture `indice` vem de lá)

EVIDENCIA = (
    "Consulta: proporção. numerador=25005 denominador=35423 valor=0.7058972983654688 n=35423 ano_compra=2026. "
    "Publicações de 2026-01-01 a 2026-08-31. Valor global 1062796.8855692174. Lei 14.133/2021, art. 75. Processo 2025-00123."
)


def _nao_conferidos(resposta, evidencia=EVIDENCIA, pergunta=""):
    return numeros_nao_conferidos(resposta, [evidencia], pergunta)


@pytest.mark.parametrize("token,esperado", [
    ("25005", [(25005.0, 0)]), ("25.005", [(25005.0, 0), (25.005, 3)]), ("1.062.796,89", [(1062796.89, 2)]), ("70,6", [(70.6, 1)]),
    ("0.7059", [(0.7059, 4)]), ("14.133", [(14133.0, 0), (14.133, 3)]), ("1.062.796", [(1062796.0, 0)]),
])
def test_interpretacoes_de_um_token_trazem_o_valor_e_a_precisao_escrita(token, esperado):
    assert interpretacoes(token) == esperado


def test_token_com_espaco_de_milhar_inclui_o_valor_junto_e_as_partes():
    valores = {v for v, _ in interpretacoes("25 005")}
    assert {25005.0, 25.0, 5.0} <= valores


@pytest.mark.parametrize("resposta", [
    "Foram 25.005 contratações [1].",                                   # milhar com ponto
    "Foram 25005 contratações [1].",                                    # sem separador
    "Foram 25 005 contratações [1].",                                   # milhar com espaço
    "A proporção é 70,6 % do total [1].",                               # fração da evidência vista como percentual, arredondada
    "A proporção é 70,59% [1].",                                        # duas casas
    "A proporção é 0,7059 [1].",                                        # a fração, arredondada a quatro casas
    "O total foi de 35.423 [1].",
    "Valor global de R$ 1.062.796,89 [1] (cerca de R$ 1,06 milhão).",   # vírgula decimal e 'milhão'
    "Valor global de 1.062.797 reais [1].",                             # arredondado ao inteiro
    "Em 2026 [1].", "De 01/01/2026 a 31/08/2026 [1].",                  # ano e partes de datas
    "Conforme a Lei 14.133/2021, art. 75 [1].",                         # referência legal
    "Processo 2025-00123 [1].",
])
def test_formas_de_escrever_o_mesmo_valor_conferem(resposta):
    assert _nao_conferidos(resposta) == []


@pytest.mark.parametrize("resposta,suspeito", [
    ("Foram 25.050 contratações [1].", "25.050"),                         # trocou dois dígitos
    ("A proporção é 71,9 % [1].", "71,9"),                                # percentual errado
    ("O total foi de 35.432 [1].", "35.432"),
    ("Valor global de R$ 2,5 milhões [1].", "2,5 milhões"),
    ("Lei 14.134/2021 [1].", "14.134"),                                   # número de lei que a evidência não traz
    ("Somando: 25.005 + 135 = 25.140 [1].", "135"),                       # conta feita pelo modelo
])
def test_numero_que_a_evidencia_nao_traz_e_apontado_como_esta_escrito(resposta, suspeito):
    assert suspeito in _nao_conferidos(resposta)


def test_inteiros_de_zero_a_dez_nao_sao_conferidos_mas_onze_ja_e():
    assert _nao_conferidos("Há 3 itens, 7 fontes e 10 casos [1].") == []
    assert _nao_conferidos("Há 11 itens [1].") == ["11"]


def test_numero_que_veio_na_pergunta_e_aceito():
    assert _nao_conferidos("Em 2024, 15 trabalhos [1].", pergunta="Quais os 15 trabalhos de 2024?") == []
    assert _nao_conferidos("Em 2024, 15 trabalhos [1].") == ["2024", "15"]


def test_citacoes_e_marcadores_de_lista_nao_viram_numero_e_o_aviso_nao_repete():
    texto = "1. Lei de 2026 [12]\n2. Outra Lei 2026 [3, 4]\n- 99 itens [1]\n- 99 itens [1]"
    assert _nao_conferidos(texto) == ["99"]


def test_extrair_devolve_interpretacoes_e_escala():
    achados = {t: (i, f) for t, i, f in extrair("1,5 milhão, 70,59, 25.005 e 3 mil")}
    assert achados["1,5 milhão"] == ([(1.5, 1)], 1_000_000.0) and achados["70,59"] == ([(70.59, 2)], 1.0)
    assert achados["25.005"] == ([(25005.0, 0), (25.005, 3)], 1.0) and achados["3 mil"] == ([(3.0, 0)], 1000.0)


# ----------------------------------------------------------------- dentro do pipeline (modelo falso)

PLANO_DOCS = json.dumps({"rota": "documentos", "naturezas": ["normativa"], "consulta_semantica": "dispensa de licitação valor"})


def test_numero_certo_na_resposta_segue_sem_segunda_tentativa_e_sem_aviso(tmp_path, indice):  # noqa: F811
    indices, emb = indice
    llm = LLMRoteirizado(PLANO_DOCS, "A dispensa está no Art. 75 [1].")
    r = responder("Dispensa de licitação?", _ctx(tmp_path, llm, indices=indices, embedder=emb))
    assert r.numeros_nao_conferidos == [] and len(llm.chamadas) == 2


def test_numero_errado_volta_ao_modelo_com_o_numero_e_corrige(tmp_path, indice):  # noqa: F811
    indices, emb = indice
    llm = LLMRoteirizado(PLANO_DOCS, "A dispensa está no Art. 99 [1].", "A dispensa está no Art. 75 [1].")
    r = responder("Dispensa de licitação?", _ctx(tmp_path, llm, indices=indices, embedder=emb))
    assert r.answer == "A dispensa está no Art. 75 [1]." and r.numeros_nao_conferidos == [] and len(llm.chamadas) == 3
    assert "99" in llm.chamadas[2][1] and "não aparecem na evidência" in llm.chamadas[2][1]


def test_numero_que_continua_errado_e_mantido_com_aviso_na_resposta_e_na_saida_de_texto(tmp_path, indice):  # noqa: F811
    indices, emb = indice
    llm = LLMRoteirizado(PLANO_DOCS, "A dispensa está no Art. 99 [1].", "A dispensa está no Art. 98 [1].")
    r = responder("Dispensa de licitação?", _ctx(tmp_path, llm, indices=indices, embedder=emb))
    assert not r.is_refusal and r.answer == "A dispensa está no Art. 98 [1]." and r.numeros_nao_conferidos == ["98"]
    assert "Atenção: estes números da resposta não foram encontrados na evidência: 98" in formatar(r)


def test_resposta_sem_aviso_nao_imprime_atencao(tmp_path, indice):  # noqa: F811
    indices, emb = indice
    llm = LLMRoteirizado(PLANO_DOCS, "A lei prevê dispensa [1].")
    assert "Atenção" not in formatar(responder("Dispensa?", _ctx(tmp_path, llm, indices=indices, embedder=emb)))
