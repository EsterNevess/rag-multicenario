"""Pedido grande demais para o provedor (HTTP 413): o cliente avisa com um erro próprio e a geração tira evidência até caber."""

from __future__ import annotations

import pytest

from src.capacidades import capacidades_do_cenario
from src.generate import Contexto, Evidencia, gerar
from src.llm import ClienteLLM, Completacao, LLMIndisponivel, PedidoGrandeDemais
from src.obs import PipelineLogger, Tokens


class _Resposta:
    def __init__(self, status: int):
        self.status_code, self.ok = status, status < 400

    headers: dict = {}  # noqa: RUF012

    def json(self):
        return {}


class _Sessao:
    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

    def post(self, *_a, **_k):
        self.chamadas += 1
        return self.respostas.pop(0)


class LLMComLimite:
    """Recusa com 413 qualquer pedido cujo texto passe de `limite` caracteres; senão responde citando a evidência [1]."""

    modelo = "fake"

    def __init__(self, limite: int):
        self.limite = limite
        self.prompts: list[str] = []

    def completar(self, sistema, usuario, *, json=False):
        self.prompts.append(usuario)
        if len(sistema) + len(usuario) > self.limite:
            raise PedidoGrandeDemais("grande demais (HTTP 413)")
        return Completacao("Há 3 registros [1].", Tokens(10, 5), self.modelo)


def _evidencias(n: int, tamanho: int = 1000) -> list[Evidencia]:
    return [Evidencia(i, f"c{i}", "base", f"Fonte {i}", f"Há 3 registros. {'x' * tamanho}") for i in range(1, n + 1)]


def _ctx(tmp_path, llm) -> Contexto:
    return Contexto(llm=llm, logger=PipelineLogger(tmp_path / "logs"), cap=capacidades_do_cenario("rj"), hoje="2026-11-22")


# ----------------------------------------------------------------- o cliente


def test_http_413_vira_pedido_grande_demais_sem_repetir():
    sessao = _Sessao(_Resposta(413))
    c = ClienteLLM("groq", "https://x/v1", "m", api_key="k", sessao=sessao, dormir=lambda _: None)
    with pytest.raises(PedidoGrandeDemais, match="413"):
        c.completar("a", "b")
    assert sessao.chamadas == 1  # repetir o mesmo pedido não adiantaria


def test_pedido_grande_demais_continua_sendo_llm_indisponivel():
    assert issubclass(PedidoGrandeDemais, LLMIndisponivel)


# ----------------------------------------------------------------- a geração


def test_tira_as_ultimas_evidencias_ate_o_pedido_caber(tmp_path):
    ctx = _ctx(tmp_path, LLMComLimite(limite=7000))  # o sistema ocupa uns 3.000; sobra para duas evidências de 1.000, não para cinco
    resultado = gerar("Quantos registros?", _evidencias(5), [], ctx)
    assert resultado is not None
    texto, usadas, _ = resultado
    assert texto.startswith("Há 3 registros") and [e.numero for e in usadas] == [1]
    final = ctx.llm.prompts[-1]
    assert "[1] Fonte 1" in final and "[5] Fonte 5" not in final
    assert "omitida" in final  # o modelo é avisado de que a evidência foi cortada
    assert len(ctx.llm.prompts) > 1


def test_pedido_que_ja_cabe_nao_e_alterado(tmp_path):
    ctx = _ctx(tmp_path, LLMComLimite(limite=100_000))
    gerar("Quantos registros?", _evidencias(3), [], ctx)
    assert len(ctx.llm.prompts) == 1 and "omitida" not in ctx.llm.prompts[0]


def test_com_uma_so_evidencia_e_ainda_grande_o_erro_sobe(tmp_path):
    ctx = _ctx(tmp_path, LLMComLimite(limite=500))
    with pytest.raises(PedidoGrandeDemais):
        gerar("Quantos registros?", _evidencias(3), [], ctx)


def test_a_citacao_de_evidencia_cortada_nao_vale(tmp_path):
    class CitaACortada(LLMComLimite):
        def completar(self, sistema, usuario, *, json=False):
            c = super().completar(sistema, usuario, json=json)
            return Completacao("Há 3 registros [1][5].", c.tokens, c.modelo)

    ctx = _ctx(tmp_path, CitaACortada(limite=7000))
    resultado = gerar("Quantos registros?", _evidencias(5), [], ctx)
    assert resultado is not None
    texto, usadas, _ = resultado
    assert "[5]" not in texto and [e.numero for e in usadas] == [1]
