"""HTTP 400 do provedor (por exemplo JSON inválido no modo JSON): o analisador tenta de novo em vez de derrubar a pergunta."""

from __future__ import annotations

import json as json_lib
import sqlite3

import pytest

from src.capacidades import capacidades_do_cenario
from src.generate import Contexto, PerguntaNaoEntendida, analisar
from src.llm import ClienteLLM, Completacao, LLMIndisponivel, PedidoRecusado
from src.normalize import COLUNAS_CONTRATACAO, _ddl
from src.obs import PipelineLogger, Tokens


class _Resposta:
    headers: dict = {}  # noqa: RUF012

    def __init__(self, status: int):
        self.status_code, self.ok = status, status < 400


class _Sessao:
    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

    def post(self, *_a, **_k):
        self.chamadas += 1
        return self.respostas.pop(0)


class LLMQueFalhaDepois:
    """As primeiras `falhas` chamadas levantam PedidoRecusado; as seguintes devolvem um plano válido."""

    modelo = "fake"

    def __init__(self, falhas: int):
        self.falhas = falhas
        self.usuarios: list[str] = []

    def completar(self, sistema, usuario, *, json=False):
        self.usuarios.append(usuario)
        if len(self.usuarios) <= self.falhas:
            raise PedidoRecusado("O servidor do modelo recusou o pedido (HTTP 400).")
        plano = {"rota": "estruturada", "naturezas": ["estruturada"], "entidade": "contratacao", "filtros": {}, "operacoes": ["contar"],
                 "campo_agregacao": None, "agrupar_por": [], "limite": None, "consulta_semantica": None}
        return Completacao(json_lib.dumps(plano), Tokens(10, 5), self.modelo)


@pytest.fixture()
def banco(tmp_path):
    caminho = tmp_path / "v1.sqlite"
    c = sqlite3.connect(caminho)
    c.execute(_ddl("contratacao", COLUNAS_CONTRATACAO))
    c.commit()
    c.close()
    return caminho


def _ctx(tmp_path, llm, banco) -> Contexto:
    return Contexto(llm=llm, logger=PipelineLogger(tmp_path / "logs"), cap=capacidades_do_cenario("rj"), banco=banco, hoje="2026-11-22")


# ----------------------------------------------------------------- o cliente


def test_http_400_vira_pedido_recusado_sem_repetir():
    sessao = _Sessao(_Resposta(400))
    c = ClienteLLM("groq", "https://x/v1", "m", api_key="k", sessao=sessao, dormir=lambda _: None)
    with pytest.raises(PedidoRecusado, match="400"):
        c.completar("a", "b", json=True)
    assert sessao.chamadas == 1


def test_pedido_recusado_continua_sendo_llm_indisponivel():
    assert issubclass(PedidoRecusado, LLMIndisponivel)


# ----------------------------------------------------------------- o analisador


def test_uma_recusa_do_servidor_e_seguida_de_nova_tentativa(tmp_path, banco):
    llm = LLMQueFalhaDepois(falhas=1)
    plano = analisar("Quantas contratações?", _ctx(tmp_path, llm, banco))
    assert plano.entidade == "contratacao" and len(llm.usuarios) == 2
    assert "JSON" in llm.usuarios[1] and "Quantas contratações?" in llm.usuarios[1]  # a segunda tentativa avisa e repete a pergunta


def test_recusa_em_todas_as_tentativas_vira_pergunta_nao_entendida(tmp_path, banco):
    llm = LLMQueFalhaDepois(falhas=99)
    with pytest.raises(PerguntaNaoEntendida):
        analisar("Quantas contratações?", _ctx(tmp_path, llm, banco))
    assert len(llm.usuarios) == 3  # TENTATIVAS_DO_ANALISADOR; não fica em laço


def test_outros_erros_do_provedor_continuam_subindo(tmp_path, banco):
    class SemChave:
        modelo = "fake"

        def completar(self, *_a, **_k):
            raise LLMIndisponivel("O servidor recusou a chave de API (HTTP 401).")

    with pytest.raises(LLMIndisponivel, match="401"):
        analisar("Quantas contratações?", _ctx(tmp_path, SemChave(), banco))
