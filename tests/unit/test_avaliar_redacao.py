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


def test_cobertura_medida_nao_piora_e_frase_comum_nao_e_alterada():
    """Linha de base medida em 16/11: 17 de 30 frases sensíveis protegidas e 0 de 12 comuns alteradas. Se as regras melhorarem, suba o piso."""
    r = avaliar("saude")
    assert r["frases_comuns_alteradas"] == 0
    assert r["frases_protegidas"] >= 17 and r["valores_protegidos"] >= 20
    assert r["frases_sensiveis"] == 30 and r["valores_total"] == 35


@pytest.mark.parametrize("id_,valor", [("S01", "Antônio Carlos Neves"), ("S19", "asma"), ("S22", "12/03/1958")])
def test_lacunas_conhecidas_continuam_documentadas(id_, valor):
    """Documenta o que as regras NÃO pegam hoje; se alguma passar a pegar, o teste avisa para atualizar a documentação."""
    sobrou = {s["id"]: s["sobrou"] for s in avaliar("saude")["sensiveis"]}
    assert valor in sobrou[id_]
