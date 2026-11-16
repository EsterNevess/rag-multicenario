"""Avaliação da resposta final: conferência de rota, de números e de estabilidade entre repetições."""

from __future__ import annotations

import sqlite3

import pytest

from src.avaliar_respostas import aparece, carregar_perguntas, estabilidade, numeros_da_resposta, numeros_esperados, rota_ok, tipo_de_evidencia


def test_numeros_da_resposta_le_milhar_com_ponto_e_decimal_com_virgula():
    assert numeros_da_resposta("Foram 25.005 de 35.423 (70,6%).") == [25005.0, 35423.0, 70.6]


@pytest.mark.parametrize("esperado, texto, esperado_ok", [
    (25005, "Foram 25.005 contratações", True),
    (0.7059, "a proporção é 70,6%", True),     # fração escrita como percentual
    (70.6, "a proporção é 0,706", True),        # percentual escrito como fração
    (314, "são 413 apresentações", False),
    (314, "sem nenhum número", False),
])
def test_aparece_aceita_percentual_e_fracao_e_recusa_numero_diferente(esperado, texto, esperado_ok):
    assert aparece(esperado, texto) is esperado_ok


def test_tipo_de_evidencia_de_cada_rota():
    assert tipo_de_evidencia(["documentos"]) == {"normativa", "conversacional"}
    assert tipo_de_evidencia(["agregada"]) == {"agregada"}
    assert tipo_de_evidencia(["fora_do_escopo"]) == set()
    assert "estruturada" in tipo_de_evidencia(["hibrida"])


@pytest.mark.parametrize("esperado, rotas, recusou, ok", [
    ([], ["fora_do_escopo"], True, True),
    ([], [], True, True),                       # recusa por regra de entrada, sem rota
    ([], ["documentos"], False, False),         # devia recusar e respondeu
    (["normativa"], ["documentos"], False, True),
    (["normativa"], ["estruturada"], False, False),
    (["normativa"], ["fora_do_escopo"], True, False),   # recusou o que devia responder
    (["agregada", "estruturada"], ["agregada"], False, True),
])
def test_rota_ok(esperado, rotas, recusou, ok):
    assert rota_ok(esperado, rotas, recusou) is ok


def test_estabilidade_ignora_repeticoes_com_erro_e_conta_respostas_distintas():
    e = estabilidade([
        {"rotas": ["documentos"], "recusa": False, "resposta": "a"},
        {"rotas": ["documentos"], "recusa": False, "resposta": "b"},
        {"erro": "HTTP 429"},
    ])
    assert e == {"repeticoes_completas": 2, "mesma_rota": True, "mesma_recusa": True, "respostas_distintas": 2}
    assert estabilidade([{"rotas": ["a"], "recusa": False, "resposta": "x"}, {"rotas": ["b"], "recusa": True, "resposta": "x"}])["mesma_rota"] is False
    assert estabilidade([{"erro": "x"}])["mesma_rota"] is None


def test_numeros_esperados_executa_cada_consulta_em_modo_somente_leitura(tmp_path):
    banco = tmp_path / "b.sqlite"
    c = sqlite3.connect(banco)
    c.execute("create table t (x integer)")
    c.executemany("insert into t values (?)", [(1,), (2,), (3,)])
    c.commit()
    c.close()
    assert numeros_esperados(banco, ["select count(*) from t", "select sum(x) from t"]) == [3.0, 6.0]
    assert numeros_esperados(None, ["select 1"]) == [] and numeros_esperados(banco, []) == []
    with pytest.raises(sqlite3.OperationalError):
        numeros_esperados(banco, ["insert into t values (9)"])


@pytest.mark.parametrize("cenario", ["rj", "saude", "pesquisa"])
def test_as_perguntas_do_cenario_estao_bem_formadas_e_sem_marcador_aberto(cenario):
    perguntas = carregar_perguntas(cenario)
    assert len({p["id"] for p in perguntas}) == len(perguntas) >= 10
    for p in perguntas:
        assert "{" not in p["pergunta"] and isinstance(p["esperado"], list), p["id"]


def test_retomar_pula_o_que_ja_foi_feito_e_refaz_o_que_falhou(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    import src.avaliar_respostas as ar

    monkeypatch.setattr(ar, "RAIZ", tmp_path)
    monkeypatch.setattr(ar, "PAUSA_ENTRE_CHAMADAS", 0)
    monkeypatch.setattr(ar, "ESPERA_NO_LIMITE", 0)
    cen = tmp_path / "scenarios" / "x" / "eval"
    cen.mkdir(parents=True)
    (cen / "respostas.json").write_text(json.dumps({"perguntas": [{"id": "A", "pergunta": "pa", "esperado": ["normativa"]}, {"id": "B", "pergunta": "pb", "esperado": []}]}), encoding="utf-8")
    (tmp_path / "logs").mkdir()
    anterior = {"cenario": "x", "tokens": {"entrada": 10, "saida": 2}, "itens": [
        {"id": "A", "execucoes": [{"rep": 1, "rotas": ["documentos"], "recusa": False, "resposta": "ok", "rota_ok": True}]},
        {"id": "B", "execucoes": [{"rep": 1, "erro": "LLMIndisponivel: HTTP 429"}]},
    ]}
    (tmp_path / "logs" / "avaliacao-respostas-x.json").write_text(json.dumps(anterior), encoding="utf-8")
    chamadas = []

    class Ctx:
        tokens = SimpleNamespace(prompt=5, completion=1)

    def responder(pergunta, ctx):
        chamadas.append(pergunta)
        return SimpleNamespace(rotas=[SimpleNamespace(value="fora_do_escopo")], is_refusal=True, refusal_reason="fora_do_escopo", sources_used=[], answer="não")

    import config
    import src.fetch_corpus as fc
    import src.generate as gen
    import src.router as rt

    monkeypatch.setattr(config, "Settings", lambda **k: None)
    monkeypatch.setattr(fc, "pastas_do_cenario", lambda c: None)
    monkeypatch.setattr(gen, "carregar_contexto", lambda cfg, pastas: Ctx())
    monkeypatch.setattr(gen, "responder", responder)
    monkeypatch.setattr(rt, "localizar_banco", lambda pastas: (_ for _ in ()).throw(FileNotFoundError()))
    r = ar.rodar("x", 1, retomar=True)
    assert chamadas == ["pb"]  # A já estava feita; B tinha falhado
    assert r["tokens"] == {"entrada": 15, "saida": 3} and r["interrompido"] is None
    assert [len(i["execucoes"]) for i in r["itens"]] == [1, 1]
