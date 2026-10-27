import json

import pytest

from src.obs import PipelineLogger, Stage, Tokens, tokens_de_uso


def _linhas(log_dir):
    arquivos = list(log_dir.glob("pipeline-*.jsonl"))
    assert len(arquivos) == 1
    return [json.loads(linha) for linha in arquivos[0].read_text(encoding="utf-8").splitlines()]


def test_registra_uma_linha_por_etapa_com_os_campos_obrigatorios(tmp_path):
    logger = PipelineLogger(tmp_path, run_id="abc123", query_id="q_1")
    with logger.etapa(Stage.QUERY_ANALYSIS, input="Quantas contratações em 2026?") as etapa:
        etapa.output = "rota estruturada"
        etapa.modelo = "modelo-x"
        etapa.tokens = Tokens(prompt=100, completion=20)
        etapa.extra = {"correcoes": []}

    (linha,) = _linhas(tmp_path)
    assert linha["run_id"] == "abc123"
    assert linha["query_id"] == "q_1"
    assert linha["stage"] == "query_analysis"
    assert linha["status"] == "ok"
    assert linha["modelo"] == "modelo-x"
    assert linha["tokens"] == {"prompt": 100, "completion": 20, "total": 120}
    assert linha["latencia_ms"] >= 0
    assert {"ts", "input", "output", "extra"} <= set(linha)


def test_erro_registra_status_erro_e_relanca(tmp_path):
    logger = PipelineLogger(tmp_path)
    with pytest.raises(ValueError, match="plano inválido"):
        with logger.etapa(Stage.ROTEAMENTO, input="pergunta"):
            raise ValueError("plano inválido")

    (linha,) = _linhas(tmp_path)
    assert linha["status"] == "erro"
    assert "ValueError" in linha["output"]


def test_dados_pessoais_sao_mascarados_no_log(tmp_path):
    logger = PipelineLogger(tmp_path)
    logger.registrar(
        Stage.GUARDRAIL,
        "ok",
        1,
        input="Meu CPF é 123.456.789-09 e meu e-mail é joao@exemplo.com",
    )

    (linha,) = _linhas(tmp_path)
    assert "123.456.789-09" not in linha["input"]
    assert "joao@exemplo.com" not in linha["input"]
    assert "[CPF]" in linha["input"]
    assert "[EMAIL]" in linha["input"]


def test_texto_longo_e_truncado(tmp_path):
    logger = PipelineLogger(tmp_path)
    logger.registrar(Stage.GERACAO, "ok", 1, output="a" * 5000)

    (linha,) = _linhas(tmp_path)
    assert len(linha["output"]) <= 501


def test_etapas_da_mesma_pergunta_compartilham_o_run_id(tmp_path):
    logger = PipelineLogger(tmp_path, run_id="r1")
    with logger.etapa(Stage.GUARDRAIL):
        pass
    with logger.etapa(Stage.ROTEAMENTO):
        pass

    linhas = _linhas(tmp_path)
    assert [linha["stage"] for linha in linhas] == ["guardrail", "roteamento"]
    assert {linha["run_id"] for linha in linhas} == {"r1"}


def test_nome_de_etapa_invalido_e_rejeitado(tmp_path):
    logger = PipelineLogger(tmp_path)
    with pytest.raises(ValueError):
        logger.registrar("etapa_inventada", "ok", 1)


def test_tokens_de_uso_aceita_objeto_dicionario_e_none():
    class Uso:
        prompt_tokens = 7
        completion_tokens = 3

    assert tokens_de_uso(Uso()).total == 10
    assert tokens_de_uso({"prompt_tokens": 5, "completion_tokens": 1}).total == 6
    assert tokens_de_uso(None).total == 0
