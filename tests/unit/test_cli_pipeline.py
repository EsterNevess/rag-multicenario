"""Linhas de comando de ingest, index, normalize, fetch_corpus e avaliar_respostas, com as funções pesadas trocadas por falsas.

O que se confere aqui é a camada fina de cada `main`: que argumentos passam adiante, o que é impresso e qual código de saída vem,
inclusive nos caminhos de erro (sem versão coletada, índice ausente, falha de coleta). As funções por trás têm os seus próprios testes.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import avaliar_respostas, fetch_corpus, index, ingest, normalize
from src.index import EmbeddingIndisponivel
from src.schema import Rota
from tests.fakes import EmbedderPalavras
from tests.unit.test_retrieve import CORPUS, _chunk, _indice


@pytest.fixture()
def pastas(tmp_path):
    return SimpleNamespace(cenario=tmp_path / "scenarios" / "rj", dados=tmp_path / "data", cenario_id="rj")


# ----------------------------------------------------------------- ingest


@pytest.fixture()
def ingest_falso(monkeypatch, pastas):
    chamadas = []
    monkeypatch.setattr(ingest, "pastas_do_cenario", lambda cenario: pastas)
    monkeypatch.setattr(ingest, "versoes_existentes", lambda p: ["v1", "v2"])
    relatorio = {"versao": "v2", "base_id": "editais_normativo", "total_chunks": 5, "chunks_com_dado_pessoal": 1, "avisos": ["data difere"],
                 "documentos": [{"chunks": 3, "doc_type": "lei", "arquivo": "lei.html"}, {"chunks": 2, "doc_type": "decreto", "arquivo": "d.pdf"}]}
    monkeypatch.setattr(ingest, "get_settings", lambda: SimpleNamespace(redacao_ner=False))
    monkeypatch.setattr(ingest, "ingerir", lambda *a: chamadas.append(a) or relatorio)
    return chamadas


def test_ingest_usa_a_versao_mais_recente_e_o_manifesto_do_cenario_e_imprime_o_resumo(ingest_falso, pastas, capsys):
    assert ingest.main(["--scenario", "rj"]) == 0
    manifesto, versao, base, p, ner = ingest_falso[0]
    assert manifesto == pastas.cenario / "corpus_manifest.yaml" and versao == "v2" and base == "editais_normativo" and p is pastas
    assert ner is None  # o NER é opcional e vem desligado
    saida = capsys.readouterr().out
    assert "v2 / editais_normativo: 5 trechos, 1 com dado pessoal mascarado." in saida and "lei.html" in saida and "AVISO: data difere" in saida


def test_ingest_respeita_versao_base_e_manifesto_dados_na_linha_de_comando(ingest_falso):
    ingest.main(["--scenario", "saude", "--versao", "v1", "--base", "pcdt_normativo", "--manifest", "outro.yaml"])
    assert ingest_falso[0][:3] == (Path("outro.yaml"), "v1", "pcdt_normativo")


def test_ingest_sem_versao_coletada_sai_com_2(monkeypatch, pastas, capsys):
    monkeypatch.setattr(ingest, "pastas_do_cenario", lambda cenario: pastas)
    monkeypatch.setattr(ingest, "versoes_existentes", lambda p: [])
    assert ingest.main(["--scenario", "rj"]) == 2
    assert "nenhuma versão coletada" in capsys.readouterr().err


def test_ingest_com_erro_de_arquivo_ou_valor_sai_com_2(monkeypatch, pastas, capsys):
    monkeypatch.setattr(ingest, "pastas_do_cenario", lambda cenario: pastas)
    monkeypatch.setattr(ingest, "versoes_existentes", lambda p: ["v1"])

    def falha(*a):
        raise FileNotFoundError("sem PDF")

    monkeypatch.setattr(ingest, "ingerir", falha)
    assert ingest.main(["--scenario", "rj"]) == 2 and "sem PDF" in capsys.readouterr().err


# ----------------------------------------------------------------- normalize


def _relatorio(**extra):
    base = {"tabelas": {"contratacao": 10, "contrato": 4}, "periodo_de_publicacao": {"de": "2026-01-01", "ate": "2026-08-31"}, "rejeitados": {"a": 1, "b": 2}}
    return {**base, **extra}


@pytest.fixture()
def normalize_pastas(monkeypatch, pastas):
    monkeypatch.setattr(normalize, "pastas_do_cenario", lambda cenario: pastas)
    monkeypatch.setattr(normalize, "versoes_existentes", lambda p: ["v3"])


def test_normalize_rj_imprime_o_resumo_do_pncp(monkeypatch, normalize_pastas, capsys):
    rel = _relatorio(presentes_em_mais_de_uma_fonte=7, com_conteudo_diferente_entre_fontes=2, fornecedores_pessoa_fisica_mascarados=3,
                     contratos_com_contratacao_encontrada={"percentual": 98.5})
    monkeypatch.setattr(normalize, "normalizar", lambda manifesto, versao, p: rel)
    assert normalize.main(["--scenario", "rj"]) == 0
    saida = capsys.readouterr().out
    assert "v3: 10 em contratacao, 4 em contrato." in saida and "rejeitados: 3" in saida
    assert "em mais de uma fonte: 7 | com conteúdo diferente: 2" in saida and "CPF de fornecedor mascarado: 3" in saida and "98.5%" in saida


def test_normalize_saude_usa_o_normalizador_da_saude_e_imprime_o_resumo_da_rename(monkeypatch, normalize_pastas, capsys):
    monkeypatch.setattr("src.normalize_saude.normalizar", lambda manifesto, versao, p: _relatorio(medicamentos_distintos=541, linhas_sem_codigo_atc=0, linhas_sem_forma=10))
    assert normalize.main(["--scenario", "saude"]) == 0
    assert "medicamentos distintos: 541 | linhas sem código ATC: 0 | sem forma: 10" in capsys.readouterr().out


def test_normalize_pesquisa_usa_o_normalizador_do_openalex(monkeypatch, normalize_pastas, capsys):
    monkeypatch.setattr("src.normalize_openalex.normalizar", lambda manifesto, versao, p: _relatorio(
        trabalhos_sem_instituicao_na_resposta=12, trabalhos_com_financiador=3000, trabalhos_com_resumo=4500))
    assert normalize.main(["--scenario", "pesquisa", "--versao", "v2"]) == 0
    assert "com financiador: 3000 | com resumo: 4500" in capsys.readouterr().out


def test_normalize_sem_versao_ou_com_arquivo_ausente_sai_com_2(monkeypatch, pastas, capsys):
    monkeypatch.setattr(normalize, "pastas_do_cenario", lambda cenario: pastas)
    monkeypatch.setattr(normalize, "versoes_existentes", lambda p: [])
    assert normalize.main(["--scenario", "rj"]) == 2 and "nenhuma versão coletada" in capsys.readouterr().err

    def falha(*a):
        raise FileNotFoundError("sem o banco")

    monkeypatch.setattr(normalize, "normalizar", falha)
    assert normalize.main(["--scenario", "rj", "--versao", "v1"]) == 2 and "sem o banco" in capsys.readouterr().err


# ----------------------------------------------------------------- index


@pytest.fixture()
def config_falsa(monkeypatch):
    config = SimpleNamespace(embedding_provider="fake", embedding_model="palavras", embedding_dimensions=64, embedding_batch_size=8, embedding_pause_seconds=0)
    monkeypatch.setattr(index, "get_settings", lambda: config)
    return config


def test_index_status_mostra_vetores_e_identidade_do_indice(tmp_path, monkeypatch, config_falsa, capsys):
    _indice(tmp_path, CORPUS, nome="idx")
    monkeypatch.setattr(index, "pasta_do_indice", lambda cenario, base, dados=None: tmp_path / "idx")
    assert index.main(["status", "--scenario", "rj", "--base", "b"]) == 0
    saida = capsys.readouterr().out
    assert saida.startswith("rj/b: 5 vetores, fake:palavras:64, criado em")


def test_index_status_de_indice_ausente_ou_de_outro_modelo_sai_com_2(tmp_path, monkeypatch, config_falsa, capsys):
    monkeypatch.setattr(index, "pasta_do_indice", lambda cenario, base, dados=None: tmp_path / "nao_existe")
    assert index.main(["status", "--scenario", "rj"]) == 2 and "Erro" in capsys.readouterr().err
    _indice(tmp_path, CORPUS, nome="idx")
    monkeypatch.setattr(index, "pasta_do_indice", lambda cenario, base, dados=None: tmp_path / "idx")
    config_falsa.embedding_model = "outro"
    assert index.main(["status", "--scenario", "rj"]) == 2


@pytest.fixture()
def build_falso(tmp_path, monkeypatch, config_falsa, pastas):
    monkeypatch.setattr(index, "pastas_do_cenario", lambda cenario: pastas)
    monkeypatch.setattr(index, "versoes_existentes", lambda p: ["v1", "v2"])
    monkeypatch.setattr(index, "pasta_do_indice", lambda cenario, base, dados=None: tmp_path / "saida")
    monkeypatch.setattr(index, "criar_embedder", lambda config: EmbedderPalavras())
    destino = pastas.dados / "processed" / "rj"
    destino.mkdir(parents=True)
    return destino


def test_index_build_indexa_a_versao_mais_recente_e_imprime_o_resultado(build_falso, tmp_path, capsys):
    (build_falso / "v2_b.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in CORPUS), encoding="utf-8")
    assert index.main(["build", "--scenario", "rj", "--base", "b"]) == 0
    assert "rj/b: 5 vetores (5 calculados agora)." in capsys.readouterr().out
    assert (tmp_path / "saida" / "estado_indexacao.json").exists()
    assert index.main(["build", "--scenario", "rj", "--base", "b"]) == 0
    assert "(0 calculados agora)" in capsys.readouterr().out  # o cache em disco evita recalcular


def test_index_build_sem_trechos_ou_sem_o_servico_de_vetores_sai_com_2(build_falso, monkeypatch, capsys):
    assert index.main(["build", "--scenario", "rj", "--base", "b", "--versao", "v9"]) == 2
    assert "Rode primeiro: python -m src.ingest" in capsys.readouterr().err
    (build_falso / "v2_b.jsonl").write_text(json.dumps(_chunk("c1", "texto"), ensure_ascii=False) + "\n", encoding="utf-8")

    def sem_ollama(config):
        raise EmbeddingIndisponivel("Ollama fora do ar")

    monkeypatch.setattr(index, "criar_embedder", sem_ollama)
    assert index.main(["build", "--scenario", "rj", "--base", "b"]) == 2 and "Ollama fora do ar" in capsys.readouterr().err


# ----------------------------------------------------------------- fetch_corpus


def _inventario(falhas=0):
    return {"version": "v2", "scenario_id": "rj", "totais": {"arquivos": 9, "bytes": 5 * 1024 * 1024, "falhas": falhas}}


@pytest.fixture()
def fetch_falso(monkeypatch, pastas):
    chamadas = {}
    monkeypatch.setattr(fetch_corpus, "pastas_do_cenario", lambda cenario: pastas)
    monkeypatch.setattr(fetch_corpus, "inventory", lambda m, v, p: chamadas.setdefault("inventory", (m, v)) and _inventario())
    monkeypatch.setattr(fetch_corpus, "fetch", lambda m, v, p, **o: chamadas.setdefault("fetch", (m, v, o)) and _inventario(falhas=chamadas.get("falhas", 0)))
    monkeypatch.setattr(fetch_corpus, "update", lambda m, p, **o: chamadas.setdefault("update", o) and (_inventario(), Path("DIFF_v1_v2.md")))
    return chamadas


def test_fetch_repassa_versao_bases_e_opcoes_e_imprime_o_inventario(fetch_falso, pastas, capsys):
    assert fetch_corpus.main(["fetch", "--scenario", "rj", "--versao", "v2", "--base", "a", "--base", "b", "--max-paginas", "3", "--pasta-local", "manual"]) == 0
    manifesto, versao, opcoes = fetch_falso["fetch"]
    assert manifesto == pastas.cenario / "corpus_manifest.yaml" and versao == "v2"
    assert opcoes["bases"] == ["a", "b"] and opcoes["max_paginas"] == 3 and opcoes["pasta_local"] == Path("manual") and opcoes["snapshot_db"] is None
    assert "v2: 9 arquivo(s), 5.0 MB, 0 falha(s)." in capsys.readouterr().out.replace("5,0", "5.0")


def test_fetch_com_falha_de_download_sai_com_1(fetch_falso):
    fetch_falso["falhas"] = 2
    assert fetch_corpus.main(["fetch", "--scenario", "rj"]) == 1


def test_update_imprime_o_diff_e_inventory_so_regera_o_inventario(fetch_falso, capsys):
    assert fetch_corpus.main(["update", "--scenario", "saude"]) == 0
    assert "Diff: DIFF_v1_v2.md" in capsys.readouterr().out
    assert fetch_corpus.main(["inventory", "--scenario", "saude", "--versao", "v1"]) == 0
    assert fetch_falso["inventory"][1] == "v1"


@pytest.mark.parametrize("erro", [fetch_corpus.ManifestoInvalido("manifesto quebrado"), fetch_corpus.VersaoImutavel("v1 é imutável"), FileNotFoundError("sem versão")])
def test_fetch_com_manifesto_invalido_versao_imutavel_ou_arquivo_ausente_sai_com_2(monkeypatch, pastas, capsys, erro):
    monkeypatch.setattr(fetch_corpus, "pastas_do_cenario", lambda cenario: pastas)

    def falha(*a, **o):
        raise erro

    monkeypatch.setattr(fetch_corpus, "fetch", falha)
    assert fetch_corpus.main(["fetch", "--scenario", "rj"]) == 2
    assert str(erro) in capsys.readouterr().err


# ----------------------------------------------------------------- avaliar_respostas


@pytest.fixture()
def rodada(tmp_path, monkeypatch):
    """Duas perguntas e um modelo que a cada chamada devolve a próxima resposta (ou levanta o erro) da fila."""
    from src import generate, router
    from src.llm import LLMIndisponivel
    from src.schema import RAGResponse

    fila = []
    perguntas = [
        {"id": "Q1", "pergunta": "Pergunta um?", "esperado": ["normativa"]},
        {"id": "Q2", "pergunta": "Pergunta dois?", "esperado": []},
    ]
    monkeypatch.setattr(avaliar_respostas, "RAIZ", tmp_path)
    monkeypatch.setattr(avaliar_respostas, "carregar_perguntas", lambda cenario: [dict(p) for p in perguntas])
    monkeypatch.setattr(avaliar_respostas.time, "sleep", lambda s: None)
    ctx = SimpleNamespace(tokens=SimpleNamespace(prompt=100, completion=20))
    monkeypatch.setattr(generate, "carregar_contexto", lambda config, pastas: ctx)

    def sem_banco(pastas):
        raise FileNotFoundError

    monkeypatch.setattr(router, "localizar_banco", sem_banco)

    def responder(pergunta, contexto):
        item = fila.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(generate, "responder", responder)
    resposta_ok = RAGResponse(answer="Resposta [1].", rotas=[Rota.DOCUMENTOS])
    recusa = RAGResponse(answer="Não.", is_refusal=True, refusal_reason="fora_do_escopo", rotas=[Rota.FORA_DO_ESCOPO])
    return SimpleNamespace(fila=fila, ok=resposta_ok, recusa=recusa, limite=LLMIndisponivel("HTTP 429"), destino=tmp_path / "logs" / "avaliacao-respostas-rj.json")


def test_rodada_completa_mede_rota_e_recusa_e_grava_o_resultado(rodada, capsys):
    rodada.fila.extend([rodada.ok, rodada.recusa])
    r = avaliar_respostas.rodar("rj", 1)
    assert r["interrompido"] is None and [i["rota_ok"] for i in r["itens"]] == [[True], [True]]  # Q1 respondeu por documentos; Q2 devia recusar e recusou
    assert r["tokens"] == {"entrada": 100, "saida": 20} and json.loads(rodada.destino.read_text(encoding="utf-8"))["cenario"] == "rj"
    assert "[rep 1] Q1" in capsys.readouterr().out


def test_limite_do_provedor_espera_uma_vez_e_depois_interrompe_a_rodada(rodada):
    rodada.fila.extend([rodada.ok, rodada.limite, rodada.limite])
    r = avaliar_respostas.rodar("rj", 1)
    assert r["interrompido"] == "limite de uso do provedor na repetição 1, pergunta Q2"
    assert len(r["itens"][0]["execucoes"]) == 1 and "erro" in r["itens"][1]["execucoes"][0]


def test_retomar_guarda_o_que_deu_certo_e_refaz_so_o_que_faltou(rodada):
    rodada.fila.extend([rodada.ok, rodada.limite, rodada.limite])
    avaliar_respostas.rodar("rj", 1)
    rodada.fila.extend([rodada.recusa])  # só a Q2 precisa de uma chamada nova
    r = avaliar_respostas.rodar("rj", 1, retomar=True)
    assert r["interrompido"] is None and [len(i["execucoes"]) for i in r["itens"]] == [1, 1] and rodada.fila == []
    q1, q2 = (i["execucoes"][0] for i in r["itens"])
    assert "erro" not in q1 and "erro" not in q2
    assert q1["resposta"] == "Resposta [1]." and q2["recusa"] is True  # a Q1 ficou como estava; só a Q2 foi refeita
    assert r["tokens"]["entrada"] == 200  # soma o que já estava gravado e o que gastou agora


def test_linha_de_comando_da_rodada_imprime_tokens_e_se_foi_interrompida(monkeypatch, capsys):
    monkeypatch.setattr(avaliar_respostas, "rodar", lambda cenario, rep, retomar: {"tokens": {"entrada": 1, "saida": 2}, "interrompido": "limite"})
    assert avaliar_respostas.main(["--scenario", "saude", "--repeticoes", "2", "--retomar"]) == 0
    assert "saude: tokens {'entrada': 1, 'saida': 2} | interrompido: limite" in capsys.readouterr().out


# ----------------------------------------------------------------- indicadores e synth


def test_indicadores_build_usa_a_versao_mais_recente_com_banco_normalizado(monkeypatch, pastas, capsys):
    from src import indicadores

    normalizado = pastas.dados / "normalized" / "rj"
    normalizado.mkdir(parents=True)
    (normalizado / "v2.sqlite").write_bytes(b"")  # a v3 não tem banco normalizado: tem de usar a v2
    monkeypatch.setattr(indicadores, "pastas_do_cenario", lambda cenario: pastas)
    monkeypatch.setattr(indicadores, "versoes_existentes", lambda p: ["v1", "v2", "v3"])
    usadas = []
    monkeypatch.setattr(indicadores, "construir", lambda cenario, versao, p: usadas.append(versao) or {"indicadores": 7, "linhas_por_indicador": {"a": 8}})
    assert indicadores.main(["build", "--scenario", "rj"]) == 0
    assert usadas == ["v2"] and "rj/v2: 7 indicadores, linhas por indicador: {'a': 8}" in capsys.readouterr().out


def test_indicadores_build_sem_banco_ou_com_catalogo_invalido_sai_com_2(monkeypatch, pastas, capsys):
    from src import indicadores

    monkeypatch.setattr(indicadores, "pastas_do_cenario", lambda cenario: pastas)
    monkeypatch.setattr(indicadores, "versoes_existentes", lambda p: ["v1"])
    assert indicadores.main(["build", "--scenario", "rj"]) == 2 and "nenhuma versão com banco normalizado" in capsys.readouterr().err

    def invalido(cenario, versao, p):
        raise indicadores.IndicadoresInvalidos("catálogo quebrado")

    monkeypatch.setattr(indicadores, "construir", invalido)
    assert indicadores.main(["build", "--scenario", "rj", "--versao", "v1"]) == 2 and "catálogo quebrado" in capsys.readouterr().err


def test_synth_build_gera_a_base_e_verify_confirma_ou_aponta_arquivo_alterado(monkeypatch, tmp_path, capsys):
    from src import synth

    pasta = tmp_path / "base_sintetica"
    monkeypatch.setattr(synth, "pasta_da_base", lambda cenario: pasta)
    assert synth.main(["verify", "--scenario", "pesquisa"]) == 2 and "Rode: python -m src.synth build" in capsys.readouterr().err
    assert synth.main(["build", "--scenario", "pesquisa"]) == 0
    assert "semente 20261029" in capsys.readouterr().out
    assert synth.main(["verify", "--scenario", "pesquisa"]) == 0 and "idêntica ao que a semente gera" in capsys.readouterr().out
    um_arquivo = next(p for p in sorted(pasta.glob("*.txt")))
    um_arquivo.write_text(um_arquivo.read_text(encoding="utf-8") + "\nlinha extra", encoding="utf-8")
    assert synth.main(["verify", "--scenario", "pesquisa"]) == 1 and "DIFERENÇA:" in capsys.readouterr().out
