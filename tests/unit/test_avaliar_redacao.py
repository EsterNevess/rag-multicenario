"""Medição da redação em texto novo: o conjunto está bem formado e a cobertura medida não piora."""

from __future__ import annotations

import pytest

from src.avaliar_redacao import _simples, avaliar, carregar_itens, processar
from src.capacidades import capacidades_do_cenario
from src.redacao import compilar


def test_o_conjunto_tem_frases_sensiveis_com_valores_que_estao_no_texto_e_frases_comuns():
    itens = carregar_itens("saude")
    assert len({i["id"] for i in itens}) == len(itens)
    sensiveis = [i for i in itens if i["tipo"] == "sensivel"]
    assert len(sensiveis) == 30 and len(itens) - len(sensiveis) == 12
    for i in sensiveis:
        assert i["valores"] and all(_simples(v) in _simples(i["texto"]) for v in i["valores"]), i["id"]


def test_processar_aplica_a_reducao_e_depois_o_mascaramento():
    regras = compilar(list(capacidades_do_cenario("saude").redacoes_na_ingestao))
    saida = processar("Aqui é a Luciana Tavares, moro na Rua das Flores 120. Meu CPF é 123.456.789-09.", regras)
    assert "Luciana" not in saida and "123.456.789-09" not in saida


def test_conjuntos_a_e_b_guiaram_as_regras_e_nao_podem_piorar_e_nenhuma_frase_comum_muda():
    """A e B foram usados para escrever as regras (não medem generalização): são guarda de regressão. Antes do ajuste: A 17 de 30, B 11 de 30."""
    for conjunto, piso in (("redacao_holdout", 30), ("redacao_holdout_b", 30)):
        r = avaliar("saude", conjunto=conjunto)
        assert r["frases_comuns_alteradas"] == 0 and r["frases_protegidas"] >= piso, conjunto


def test_conjunto_c_nunca_foi_usado_para_ajustar_regra_e_mede_a_cobertura_em_texto_novo():
    """Medida de 16/11: 13 de 30 frases sensíveis protegidas (43%) e 0 de 12 comuns alteradas. Se a cobertura subir, suba o piso, mas só
    com um conjunto D novo: depois de ajustar regras olhando para o C, ele deixa de medir."""
    r = avaliar("saude", conjunto="redacao_holdout_c")
    assert r["frases_sensiveis"] == 30 and r["frases_comuns"] == 12 and r["frases_comuns_alteradas"] == 0
    assert r["frases_protegidas"] >= 13 and r["valores_protegidos"] >= 18


@pytest.mark.parametrize("id_,valor", [("T01", "Rosinha"), ("T10", "captopril"), ("T27", "20521-110"), ("T19", "labirintite")])
def test_lacunas_conhecidas_do_conjunto_c_continuam_documentadas(id_, valor):
    """Documenta o que as regras NÃO pegam em texto novo; se alguma passar a pegar, o teste avisa para atualizar a documentação."""
    sobrou = {s["id"]: s["sobrou"] for s in avaliar("saude", conjunto="redacao_holdout_c")["sensiveis"]}
    assert valor in sobrou[id_]


def test_o_titulo_de_tratamento_abreviado_nao_separa_o_nome_da_frase():
    from src.redacao import _FIM_DE_FRASE

    assert _FIM_DE_FRASE.split("O Dr. Anderson Costa me atendeu mal. Foi ruim.") == ["O Dr. Anderson Costa me atendeu mal.", "Foi ruim."]
    assert _FIM_DE_FRASE.split("A Dra. Fernanda Lopes atendeu. A sra. Maria esperou.") == ["A Dra. Fernanda Lopes atendeu.", "A sra. Maria esperou."]
    assert _FIM_DE_FRASE.split("Fui na unidade. Esperei duas horas.") == ["Fui na unidade.", "Esperei duas horas."]
