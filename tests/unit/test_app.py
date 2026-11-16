"""Interface: a lógica (app/nucleo.py) e a tela (app/streamlit_app.py, com o AppTest do Streamlit, sem navegador)."""

from __future__ import annotations

import pytest

from pathlib import Path

from app import nucleo
from src.llm import LLMIndisponivel
from src.schema import Fonte, Natureza, PlanoConsulta, RAGResponse, Rota

SCRIPT = str(Path(__file__).resolve().parents[2] / "app" / "streamlit_app.py")

PLANO = PlanoConsulta(rota=Rota.HIBRIDA, naturezas=[Natureza.NORMATIVA, Natureza.ESTRUTURADA], entidade="medicamento",
                      filtros={"dcb__contem": "adalimumabe"}, filtros_documentos={"doenca": "artrite"}, operacoes=["listar"], limite=20)


def _ok():
    return RAGResponse(answer="O adalimumabe consta na RENAME [1].", sources_used=[Fonte(numero=1, chunk_id="c1", base_id="b", rotulo="RENAME 2024")],
                       rotas=[Rota.HIBRIDA], cobertura="de 2017 a 2026", trace_id="q_abc123", plano=PLANO)


def _recusa():
    return RAGResponse(answer="Não oriento dose.", is_refusal=True, refusal_reason="prescricao_individual", rotas=[], trace_id="q_r")


# ----------------------------------------------------------------- lógica


def test_resumir_plano_mostra_so_o_que_esta_preenchido():
    linhas = dict(nucleo.resumir_plano(PLANO))
    assert linhas["Rota"] == "hibrida" and linhas["Entidade"] == "medicamento" and linhas["Filtros"] == "dcb__contem = adalimumabe"
    assert linhas["Filtros dos documentos"] == "doenca = artrite" and linhas["Limite"] == "20"
    assert "Agrupar por" not in linhas and "Campo" not in linhas
    assert nucleo.resumir_plano(None) == []


def test_linhas_de_fontes_vem_numeradas_e_em_ordem():
    r = RAGResponse(answer="x [1][2]", sources_used=[Fonte(numero=2, chunk_id="c2", base_id="b"), Fonte(numero=1, chunk_id="c1", base_id="b", rotulo="Lei 14.133")], rotas=[Rota.DOCUMENTOS])
    assert nucleo.linhas_de_fontes(r) == ["[1] Lei 14.133", "[2] c2"]


def test_motivo_legivel_traduz_os_conhecidos_e_formata_os_outros():
    assert nucleo.motivo_legivel("fora_do_escopo") == "Fora do escopo das bases"
    assert nucleo.motivo_legivel("juizo_de_legalidade") == "Juizo de legalidade" and nucleo.motivo_legivel(None) == ""


def test_perguntar_vazio_nao_chama_o_motor(monkeypatch):
    monkeypatch.setattr(nucleo, "responder", lambda *a: pytest.fail("não deveria chamar"))
    assert nucleo.perguntar(object(), "   ").erro == "Escreva uma pergunta."


def test_perguntar_devolve_a_resposta_e_transforma_falha_do_modelo_em_mensagem(monkeypatch):
    monkeypatch.setattr(nucleo, "responder", lambda p, c: _ok())
    assert nucleo.perguntar(object(), " pergunta ").resposta.answer.startswith("O adalimumabe")

    def falha(p, c):
        raise LLMIndisponivel("HTTP 429")

    monkeypatch.setattr(nucleo, "responder", falha)
    r = nucleo.perguntar(object(), "pergunta")
    assert r.resposta is None and "429" in r.erro and "mais tarde" in r.erro


def test_cada_cenario_tem_nome_e_exemplos_e_os_exemplos_passam_na_guarda_de_entrada_ou_sao_recusas_esperadas():
    from src.capacidades import capacidades_do_cenario
    from src.router import avaliar_pergunta

    assert set(nucleo.CENARIOS) == set(nucleo.EXEMPLOS)
    recusas_esperadas = {"Esse contrato da Secretaria X foi fraude?", "Minha mãe tem 72 anos e diabetes: qual dose de insulina ela deve usar?",
                        "Quem foi o revisor do manuscrito M-014?"}
    for cenario, exemplos in nucleo.EXEMPLOS.items():
        cap = capacidades_do_cenario(cenario)
        for e in exemplos:
            decisao = avaliar_pergunta(e, cap)
            assert decisao.permitida or e in recusas_esperadas, e


# ----------------------------------------------------------------- tela


@pytest.fixture()
def tela(monkeypatch):
    from streamlit.testing.v1 import AppTest
    import streamlit as st

    st.cache_resource.clear()
    chamadas = []
    monkeypatch.setattr(nucleo, "criar_contexto", lambda cenario: chamadas.append(cenario) or object())
    monkeypatch.setattr(nucleo, "responder", lambda p, c: _recusa() if "dose" in p else _ok())
    at = AppTest.from_file(SCRIPT, default_timeout=30)
    at.chamadas = chamadas
    return at.run()


def _textos(elementos):
    return " ".join(e.value for e in elementos)


def test_tela_abre_sem_erro_no_primeiro_cenario_com_exemplos_e_aviso(tela):
    assert not tela.exception and tela.chamadas == ["rj"]
    assert len(tela.button) == len(nucleo.EXEMPLOS["rj"]) + 1  # exemplos + Perguntar
    assert "sintéticos" in _textos(tela.sidebar.caption)


def test_perguntar_mostra_resposta_fontes_cobertura_e_plano(tela):
    tela.text_area(key="pergunta").set_value("O adalimumabe consta na RENAME?")
    tela.button[-1].click()
    tela.run()
    texto = _textos(tela.markdown)
    assert "O adalimumabe consta na RENAME [1]." in texto and "[1] RENAME 2024" in texto
    assert "Cobertura dos dados: de 2017 a 2026" in _textos(tela.caption)
    assert "**Rota:** hibrida" in texto and "**Entidade:** medicamento" in texto


def test_recusa_aparece_como_aviso_com_o_motivo_e_sem_fontes(tela):
    tela.text_area(key="pergunta").set_value("qual a dose de insulina?")
    tela.button[-1].click()
    tela.run()
    assert "Não oriento dose." in _textos(tela.warning) and "Motivo: Prescricao individual" in _textos(tela.caption)
    assert "**Fontes**" not in _textos(tela.markdown)
    assert "A pergunta foi barrada antes" in _textos(tela.caption)


def test_clicar_num_exemplo_preenche_a_pergunta(tela):
    tela.button[0].click()
    tela.run()
    assert tela.text_area(key="pergunta").value == nucleo.EXEMPLOS["rj"][0]


def test_trocar_de_cenario_carrega_o_outro_e_limpa_a_pergunta(tela):
    tela.text_area(key="pergunta").set_value("algo")
    tela.run()
    tela.sidebar.selectbox[0].select("saude")
    tela.run()
    assert tela.chamadas == ["rj", "saude"] and tela.text_area(key="pergunta").value == ""
    assert len(tela.button) == len(nucleo.EXEMPLOS["saude"]) + 1


def test_falha_ao_carregar_o_cenario_vira_mensagem(monkeypatch):
    from streamlit.testing.v1 import AppTest
    import streamlit as st

    st.cache_resource.clear()

    def falha(cenario):
        raise FileNotFoundError("sem índice")

    monkeypatch.setattr(nucleo, "criar_contexto", falha)
    at = AppTest.from_file(SCRIPT, default_timeout=30).run()
    assert "Não foi possível carregar o cenário: sem índice" in _textos(at.error)
