"""Linha de comando do motor (consulta e modo interativo) e o laço de `avaliar`, com o contexto e o modelo trocados por falsos."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import avaliar as av
from src import generate
from src.llm import LLMIndisponivel
from src.rerank import RerankerIndisponivel
from src.schema import Fonte, Natureza, PlanoConsulta, RAGResponse, Rota
from tests.unit.test_retrieve import CORPUS, _indice

PLANO = PlanoConsulta(rota=Rota.DOCUMENTOS, naturezas=[Natureza.NORMATIVA], consulta_semantica="modalidades")


def _resposta(texto="Resposta [1].", recusa=False, plano=PLANO):
    if recusa:
        return RAGResponse(answer=texto, is_refusal=True, refusal_reason="fora_do_escopo", rotas=[Rota.FORA_DO_ESCOPO])
    return RAGResponse(answer=texto, sources_used=[Fonte(numero=1, chunk_id="c1", base_id="b", rotulo="Lei 14.133 — Art. 28")],
                       rotas=[Rota.DOCUMENTOS], cobertura="de 2017 a 2026", trace_id="q_x", plano=plano)


@pytest.fixture()
def cli(monkeypatch):
    """`python -m src.generate` com o contexto e o motor falsos; guarda o que o motor recebeu."""
    chamadas = []
    ctx = SimpleNamespace(logger=SimpleNamespace(query_id=None))
    monkeypatch.setattr(generate, "carregar_contexto", lambda config, pastas: ctx)
    monkeypatch.setattr(generate, "responder", lambda p, c, a=None: chamadas.append((p, a)) or _resposta(f"Resposta a {p} [1]."))
    return chamadas


# ----------------------------------------------------------------- python -m src.generate


def test_uma_pergunta_imprime_resposta_fontes_e_cobertura_e_sai_com_zero(cli, capsys):
    assert generate.main(["--scenario", "saude", "Quais as modalidades?"]) == 0
    saida = capsys.readouterr().out
    assert "Resposta a Quais as modalidades? [1]." in saida and "[1] Lei 14.133 — Art. 28" in saida and "Cobertura: de 2017 a 2026" in saida
    assert cli == [("Quais as modalidades?", None)]  # a pergunta avulsa não tem memória


def test_json_imprime_a_resposta_estruturada_com_o_plano(cli, capsys):
    generate.main(["--scenario", "rj", "--json", "pergunta"])
    dados = json.loads(capsys.readouterr().out)
    assert dados["answer"].startswith("Resposta a pergunta") and dados["plano"]["rota"] == "documentos"


def test_falha_do_modelo_vira_mensagem_e_codigo_3(monkeypatch, capsys):
    ctx = SimpleNamespace(logger=SimpleNamespace(query_id=None))
    monkeypatch.setattr(generate, "carregar_contexto", lambda config, pastas: ctx)

    def falha(p, c, a=None):
        raise LLMIndisponivel("HTTP 429")

    monkeypatch.setattr(generate, "responder", falha)
    assert generate.main(["--scenario", "rj", "pergunta"]) == 3
    assert "429" in capsys.readouterr().err


def test_cenario_que_nao_carrega_sai_com_codigo_2(monkeypatch, capsys):
    def falha(config, pastas):
        raise FileNotFoundError("sem índice")

    monkeypatch.setattr(generate, "carregar_contexto", falha)
    assert generate.main(["--scenario", "rj", "pergunta"]) == 2
    assert "sem índice" in capsys.readouterr().err


def test_modo_interativo_continua_a_conversa_e_nova_comeca_um_assunto_novo(cli, monkeypatch, capsys):
    entradas = iter(["Quantas dispensas?", "Desses, quais?", "/nova", "Outra pergunta?", ""])
    monkeypatch.setattr("builtins.input", lambda _="": next(entradas))
    assert generate.main(["--scenario", "rj"]) == 0
    assert [p for p, _ in cli] == ["Quantas dispensas?", "Desses, quais?", "Outra pergunta?"]
    assert cli[0][1] is None
    assert cli[1][1] == generate.TurnoAnterior("Quantas dispensas?", PLANO)
    assert cli[2][1] is None  # depois de /nova
    assert "/nova" in capsys.readouterr().out


def test_modo_interativo_nao_lembra_resposta_recusada_e_sai_no_fim_da_entrada(monkeypatch):
    chamadas = []
    ctx = SimpleNamespace(logger=SimpleNamespace(query_id=None))
    monkeypatch.setattr(generate, "carregar_contexto", lambda config, pastas: ctx)
    respostas = iter([_resposta("não", recusa=True), _resposta()])
    monkeypatch.setattr(generate, "responder", lambda p, c, a=None: chamadas.append(a) or next(respostas))
    entradas = iter(["primeira", "segunda"])

    def entrada(_=""):
        try:
            return next(entradas)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", entrada)
    assert generate.main(["--scenario", "rj"]) == 0
    assert chamadas == [None, None]  # a primeira foi recusa: não vira contexto da segunda


# ----------------------------------------------------------------- avaliar (laço e linha de comando)


def _item(id_="Q1", pergunta="dispensa de licitação", base="b", texto=("dispensa",), metadata=None):
    return av.Item(id_, pergunta, base, {}, texto, metadata or {})


@pytest.fixture()
def ambiente(tmp_path, monkeypatch):
    indice, embedder = _indice(tmp_path, CORPUS)
    ctx = SimpleNamespace(indices={"b": indice}, embedder=embedder, cap=SimpleNamespace(niveis_consultaveis=("publico",)), hoje="2026-11-16")
    monkeypatch.setattr(generate, "carregar_contexto", lambda config, pastas: ctx)

    def sem_modelo():
        raise RerankerIndisponivel("sem arquivos")

    monkeypatch.setattr(av, "carregar_reranker", sem_modelo)
    return ctx


def test_avaliar_mede_os_cinco_sistemas_em_cada_pergunta_valida_e_aponta_as_invalidas(ambiente, monkeypatch):
    monkeypatch.setattr(av, "carregar_gold", lambda cenario, raiz=None, conjunto="retrieval_gold": [
        _item("Q1"), _item("Q2", base="inexistente"), _item("Q3", texto=("palavra que nenhum trecho tem",)),
    ])
    r = av.avaliar("rj")
    assert r["itens_validos"] == 1 and len(r["itens_invalidos"]) == 2
    assert any("sem índice" in i for i in r["itens_invalidos"]) and any("nenhum trecho cumpre" in i for i in r["itens_invalidos"])
    assert list(r["resumo"]) == list(av.SISTEMAS) and av.SISTEMA_RERANK not in r["resumo"]
    assert all(m["deterministico"] for m in r["resumo"].values()) and r["resumo"]["rrf"]["hit@5"] == 1.0
    assert r["hoje"] == "2026-11-16" and r["itens"][0]["relevantes"] >= 1


def test_com_reranker_disponivel_o_sistema_rrf_rerank_entra_na_medicao(ambiente, monkeypatch):
    class Falso:
        def reordenar(self, consulta, candidatos):
            return [c for c, _ in reversed(candidatos)]

    monkeypatch.setattr(av, "carregar_reranker", lambda: Falso())
    monkeypatch.setattr(av, "carregar_gold", lambda cenario, raiz=None, conjunto="retrieval_gold": [_item("Q1")])
    r = av.avaliar("rj", hoje="2026-01-01")
    assert av.SISTEMA_RERANK in r["resumo"] and r["hoje"] == "2026-01-01"
    assert set(r["itens"][0]["sistemas"]) == set(av.SISTEMAS) | {av.SISTEMA_RERANK}


def test_rrf_rerank_sem_reranker_e_erro_claro(ambiente):
    indice = ambiente.indices["b"]
    with pytest.raises(ValueError, match="reranker"):
        av.ranking(av.SISTEMA_RERANK, indice, ambiente.embedder, _item(), "2026-11-16", ["publico"])


def test_linha_de_comando_do_avaliar_imprime_a_tabela_e_grava_o_resultado(ambiente, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(av, "carregar_gold", lambda cenario, raiz=None, conjunto="retrieval_gold": [_item("Q1")])
    monkeypatch.setattr(av, "RAIZ", tmp_path)
    assert av.main(["--scenario", "rj", "--conjunto", "retrieval_gold_novas"]) == 0
    saida = capsys.readouterr().out
    assert "1 perguntas válidas" in saida and "rrf_sem_filtro" in saida
    gravado = json.loads((tmp_path / "logs" / "avaliacao-recuperacao-rj-novas.json").read_text(encoding="utf-8"))
    assert gravado["itens_validos"] == 1


# ----------------------------------------------------------------- avaliar_redacao (linha de comando)


def test_linha_de_comando_da_redacao_imprime_o_resumo_e_lista_o_que_sobrou(capsys):
    from src import avaliar_redacao

    assert avaliar_redacao.main(["--scenario", "saude", "--conjunto", "redacao_holdout_c"]) == 0
    saida = capsys.readouterr().out
    assert "frases sensíveis protegidas" in saida and "SOBROU T01" in saida


# ----------------------------------------------------------------- carregar_contexto


def _pastas_vazias(tmp_path, cenario="rj"):
    from src.fetch_corpus import Pastas

    return Pastas(cenario_id=cenario, cenario=Path(__file__).resolve().parents[2] / "scenarios" / cenario, dados=tmp_path / "data")


def _config(**extra):
    from config import Settings

    return Settings(scenario="rj", llm_provider="ollama", _env_file=None, **extra)


def test_contexto_de_um_ambiente_sem_dados_nao_tem_banco_nem_indice_nem_reranker(tmp_path):
    ctx = generate.carregar_contexto(_config(log_dir=tmp_path / "logs"), _pastas_vazias(tmp_path))
    assert ctx.banco is None and ctx.banco_agregado is None and ctx.indices == {} and ctx.reranker is None
    assert ctx.naturezas == [] and ctx.cap.catalogo_de_indicadores is not None


def test_rerank_ligado_sem_os_arquivos_avisa_e_segue_sem_ele(tmp_path, capsys, monkeypatch):
    def sem_modelo():
        raise RerankerIndisponivel("sem arquivos")

    monkeypatch.setattr(generate, "carregar_reranker", sem_modelo)  # não depende de o modelo estar baixado nesta máquina
    ctx = generate.carregar_contexto(_config(log_dir=tmp_path / "logs", rerank=True), _pastas_vazias(tmp_path))
    assert ctx.reranker is None and "rerank ligado, mas indisponível" in capsys.readouterr().err


def test_rerank_ligado_com_o_modelo_disponivel_entra_no_contexto(tmp_path, monkeypatch):
    modelo = object()
    monkeypatch.setattr(generate, "carregar_reranker", lambda: modelo)
    ctx = generate.carregar_contexto(_config(log_dir=tmp_path / "logs", rerank=True), _pastas_vazias(tmp_path))
    assert ctx.reranker is modelo
