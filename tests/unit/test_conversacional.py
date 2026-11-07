"""Rota conversacional: bases de e-mails, atas e pareceres ao lado das normas, com política de acesso e guarda de saída."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.capacidades import capacidades_do_cenario
from src.generate import Contexto, montar_prompt_analise, responder
from src.index import CacheDeEmbeddings, carregar_indice, construir_indice
from src.obs import PipelineLogger
from src.router import avaliar_pergunta, avaliar_resposta
from src.schema import Natureza
from tests.fakes import EmbedderPalavras, LLMRoteirizado

PESQUISA = capacidades_do_cenario("pesquisa")
RJ = capacidades_do_cenario("rj")


def _chunk(chunk_id, texto, base, sensibilidade, natureza, **meta):
    return {"texto": texto, "metadata": {"source_file": f"{chunk_id}.txt", "doc_type": meta.pop("doc_type", "parecer"), "chunk_id": chunk_id,
                                         "sensitivity": sensibilidade, "base_id": base, "natureza": natureza, **meta}}


def _indice(tmp_path, nome, chunks, emb):
    jsonl = tmp_path / f"{nome}.jsonl"
    jsonl.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks), encoding="utf-8")
    cache = CacheDeEmbeddings(tmp_path / f"{nome}-cache.sqlite", emb.identity)
    construir_indice(jsonl, tmp_path / nome, emb, cache, tamanho_lote=8, escrever=lambda _: None)
    cache.fechar()
    return carregar_indice(tmp_path / nome, emb.identity)


@pytest.fixture()
def ambiente(tmp_path):
    emb = EmbedderPalavras()
    normas = _indice(tmp_path, "normas", [
        _chunk("n:art75", "Art. 75. É dispensável a licitação para contratação de valor baixo.", "normas", "publico", "normativa",
               doc_type="lei", norma="Lei 14.133/2021", rotulo="Art. 75"),
    ], emb)
    pareceres = _indice(tmp_path, "pareceres", [
        _chunk("p:m14r2", "Parecer do revisor 2 sobre o manuscrito M-014. A amostra é pequena e falta aprovação do comitê de ética.", "pareceres",
               "restrito", "conversacional", sintetico=True, norma="Parecer do revisor 2 sobre o manuscrito M-014", rotulo="Parecer",
               data_publicacao="2026-06-19", manuscrito_id="M-014", revisor_codigo="revisor 2"),
        _chunk("p:interno", "Ata interna sobre dispensa de licitação e o prazo do pregão.", "pareceres", "interno", "conversacional",
               sintetico=True, norma="Ata interna", doc_type="ata"),
    ], emb)
    return {"normas": normas, "pareceres": pareceres}, emb


def _ctx(tmp_path, llm, ambiente, cap):
    indices, emb = ambiente
    return Contexto(llm=llm, logger=PipelineLogger(tmp_path / "logs"), cap=cap, indices=indices, embedder=emb, hoje="2026-11-06",
                    natureza_das_bases={"normas": Natureza.NORMATIVA, "pareceres": Natureza.CONVERSACIONAL}, cobertura="x")


def _plano(**kw) -> str:
    base = {"rota": "documentos", "naturezas": ["conversacional"], "entidade": None, "filtros": {}, "operacoes": [], "campo_agregacao": None,
            "agrupar_por": [], "limite": None, "consulta_semantica": "amostra pequena comitê de ética revisor 2 manuscrito M-014"}
    base.update(kw)
    return json.dumps(base)


# ----------------------------------------------------------------- roteamento por natureza e política de acesso


def test_o_contexto_enxerga_as_duas_naturezas_de_documento(tmp_path, ambiente):
    assert _ctx(tmp_path, None, ambiente, PESQUISA).naturezas == [Natureza.NORMATIVA, Natureza.CONVERSACIONAL]


def test_o_analisador_e_avisado_de_que_existem_e_mails_atas_e_pareceres(tmp_path, ambiente):
    prompt = montar_prompt_analise(_ctx(tmp_path, None, ambiente, PESQUISA))
    assert "conversacional" in prompt and "e-mails, atas e pareceres" in prompt and "normativa" in prompt and "hibrida" in prompt


def test_plano_conversacional_busca_so_na_base_conversacional_e_cita_como_sintetico(tmp_path, ambiente):
    llm = LLMRoteirizado(_plano(), "O revisor 2 aponta amostra pequena e falta de aprovação ética [1].")
    r = responder("Que problemas o revisor 2 aponta no manuscrito M-014?", _ctx(tmp_path, llm, ambiente, PESQUISA))
    assert not r.is_refusal
    assert [f.base_id for f in r.sources_used] == ["pareceres"]
    assert r.sources_used[0].rotulo == "Parecer do revisor 2 sobre o manuscrito M-014 (documento sintético de 2026-06-19)"
    enviado = llm.chamadas[1][1]
    assert "amostra é pequena" in enviado and "Art. 75" not in enviado  # a norma não entrou na busca


def test_plano_normativo_nao_chega_aos_pareceres(tmp_path, ambiente):
    llm = LLMRoteirizado(_plano(naturezas=["normativa"], consulta_semantica="dispensa de licitação valor"), "Prevista no Art. 75 [1].")
    r = responder("Quando a licitação é dispensável?", _ctx(tmp_path, llm, ambiente, PESQUISA))
    assert [f.base_id for f in r.sources_used] == ["normas"]
    assert "revisor 2" not in llm.chamadas[1][1]


def test_rj_devolve_o_nivel_interno_mas_nao_o_restrito(tmp_path, ambiente):
    llm = LLMRoteirizado(_plano(consulta_semantica="dispensa de licitação prazo do pregão ata"), "A ata interna trata do pregão [1].")
    responder("O que diz a ata sobre a dispensa?", _ctx(tmp_path, llm, ambiente, RJ))
    enviado = llm.chamadas[1][1]
    assert "Ata interna" in enviado and "revisor 2" not in enviado


def test_pesquisa_devolve_o_nivel_restrito_mas_nao_o_interno(tmp_path, ambiente):
    llm = LLMRoteirizado(_plano(), "O revisor 2 aponta amostra pequena [1].")
    responder("Que problemas o revisor 2 aponta?", _ctx(tmp_path, llm, ambiente, PESQUISA))
    enviado = llm.chamadas[1][1]
    assert "amostra é pequena" in enviado and "Ata interna" not in enviado


def test_o_nivel_restrito_nunca_e_devolvido_quando_o_cenario_so_permite_publico(tmp_path, ambiente):
    so_publico = replace(PESQUISA, niveis_consultaveis=("publico",))
    llm = LLMRoteirizado(_plano(), "irrelevante")
    r = responder("Que problemas o revisor 2 aponta?", _ctx(tmp_path, llm, ambiente, so_publico))
    assert r.is_refusal and r.refusal_reason == "sem_evidencia" and len(llm.chamadas) == 1  # sem o que citar, o modelo de geração nem é chamado


# ----------------------------------------------------------------- guarda de saída


@pytest.mark.parametrize("resposta", [
    "O revisor 2 é da Universidade Federal Fictícia do Vale.",
    "O revisor 2 trabalha no Departamento de Física.",
    "Provavelmente o revisor 2 é o Dr. Quintanilha.",
    "A UFFV tem um revisor 2 que escreveu esse parecer.",
])
def test_resposta_que_revela_vinculo_do_revisor_e_bloqueada(resposta):
    d = avaliar_resposta(resposta, PESQUISA)
    assert not d.permitida and d.categoria == "identidade_do_revisor_na_resposta"


@pytest.mark.parametrize("resposta", [
    "O revisor 2 aponta amostra pequena, falta de aprovação ética e ausência de código.",
    "O revisor 1 recomenda revisão maior e sugere comparar com um método de referência.",
    "Segundo o parecer do revisor 3, a análise estatística não verifica a normalidade.",
])
def test_resumo_tecnico_do_parecer_passa_pela_guarda(resposta):
    assert avaliar_resposta(resposta, PESQUISA).permitida


def test_guarda_de_saida_transforma_resposta_vazada_em_recusa_sem_fontes(tmp_path, ambiente):
    llm = LLMRoteirizado(_plano(), "O revisor 2 é da Universidade Federal Fictícia do Vale e aponta amostra pequena [1].")
    r = responder("Que problemas o revisor 2 aponta no manuscrito M-014?", _ctx(tmp_path, llm, ambiente, PESQUISA))
    assert r.is_refusal and r.refusal_reason == "identidade_do_revisor_na_resposta" and r.sources_used == []
    assert "Universidade" not in r.answer


def test_cenario_sem_regras_de_saida_nao_bloqueia_nada():
    assert avaliar_resposta("O revisor 2 é da Universidade X.", RJ).permitida


# ----------------------------------------------------------------- guarda de entrada do cenário de pesquisa


@pytest.mark.parametrize("pergunta", [
    "Quem foi o revisor do manuscrito M-014?",
    "O revisor 2 é da UFRJ?",
    "De qual instituição é o revisor 2 do M-014?",
    "O revisor 2 parece ser o próprio autor do trabalho citado?",
    "Qual a universidade do parecerista que avaliou o manuscrito?",
    "O revisor conhece os autores do manuscrito?",
    "O revisor 2 do M-014 é da UFRJ?",
    "O revisor 2 do M-014 escreveu algo sobre o próprio trabalho ou o laboratório dele?",
    "No parecer do revisor 2 do M-014, quais críticas se referem a trabalhos anteriores do próprio revisor?",
])
def test_pergunta_que_tenta_identificar_o_revisor_e_barrada(pergunta):
    assert not avaliar_pergunta(pergunta, PESQUISA).permitida


@pytest.mark.parametrize("pergunta", [
    "Que problemas o revisor 2 aponta no manuscrito M-014?",
    "O que o revisor 1 recomendou sobre o manuscrito M-010?",
    "Quais manuscritos tiveram recomendação de revisão maior?",
    "O parecer do revisor 3 menciona ética em pesquisa?",
    "O revisor 2 aponta que a amostra é de pequeno porte?",
    "O revisor 2 do M-014 é favorável à publicação?",
    "Qual a recomendação dos revisores para o manuscrito M-010?",
])
def test_pergunta_sobre_o_conteudo_do_parecer_passa(pergunta):
    assert avaliar_pergunta(pergunta, PESQUISA).permitida
