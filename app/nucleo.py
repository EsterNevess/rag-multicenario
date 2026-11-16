"""Lógica da interface, sem Streamlit: criar o contexto de um cenário, perguntar e preparar o que a tela mostra.

Fica separada da tela para poder ser testada sem abrir um navegador.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:  # `streamlit run app/streamlit_app.py` põe só a pasta app no caminho
    sys.path.insert(0, str(RAIZ))

from config import Settings  # noqa: E402
from src.fetch_corpus import pastas_do_cenario  # noqa: E402
from src.generate import carregar_contexto, responder  # noqa: E402
from src.llm import LLMIndisponivel  # noqa: E402
from src.schema import PlanoConsulta, RAGResponse  # noqa: E402

CENARIOS = {
    "rj": "Contratações públicas do RJ",
    "saude": "Saúde pública (SUS)",
    "pesquisa": "Pesquisa acadêmica",
}

# Perguntas que mostram capacidades diferentes (as mesmas do DEMO.md): norma com citação, dado calculado, cruzamento e recusa.
EXEMPLOS = {
    "rj": [
        "Quais são as modalidades de licitação previstas na Lei 14.133/2021?",
        "Qual foi o valor médio por quilo de arroz contratado por municípios do RJ em 2026?",
        "Sou microempresa: tenho tratamento diferenciado em licitações e há contratações abertas de serviços de limpeza?",
        "Esse contrato da Secretaria X foi fraude?",
    ],
    "saude": [
        "Qual é a versão vigente do PCDT de doença de Parkinson e desde quando vale?",
        "O adalimumabe consta na RENAME 2024 e o PCDT vigente de artrite reumatoide o indica? Há divergência?",
        "Quantas apresentações do componente Especializado a RENAME 2024 traz?",
        "Há registros de falta de insulina humana NPH na ouvidoria?",
        "Minha mãe tem 72 anos e diabetes: qual dose de insulina ela deve usar?",
    ],
    "pesquisa": [
        "Quais os 10 trabalhos mais citados entre 2020 e 2024 de instituições do RJ sobre aprendizado de máquina?",
        "Que despesas o Edital FAPERJ 13/2023 permite na prestação de contas?",
        "Meu estudo usa apenas dados públicos anonimizados. Preciso submeter ao CEP?",
        "Que problemas o revisor 2 aponta no manuscrito M-014?",
        "Quem foi o revisor do manuscrito M-014?",
    ],
}

MOTIVOS_DE_RECUSA = {
    "fora_do_escopo": "Fora do escopo das bases",
    "sem_evidencia": "Sem evidência nas bases",
    "pergunta_nao_entendida": "Pergunta não transformada em consulta",
    "saida_bloqueada": "Resposta bloqueada pela guarda de saída",
    "sem_dados": "Nenhuma base carregada",
}


@dataclass
class Resultado:
    """O que a tela precisa: a resposta, ou o motivo de não haver uma."""

    resposta: RAGResponse | None = None
    erro: str | None = None


def criar_contexto(cenario: str) -> Any:
    return carregar_contexto(Settings(scenario=cenario), pastas_do_cenario(cenario))


def perguntar(ctx: Any, pergunta: str) -> Resultado:
    """Chama o motor. Falha do provedor do modelo vira mensagem; o resto não é engolido de forma silenciosa."""
    pergunta = pergunta.strip()
    if not pergunta:
        return Resultado(erro="Escreva uma pergunta.")
    try:
        return Resultado(resposta=responder(pergunta, ctx))
    except LLMIndisponivel as erro:
        return Resultado(erro=f"O modelo de linguagem não respondeu: {erro}. Se for o limite de uso do plano gratuito, tente de novo mais tarde.")


def motivo_legivel(codigo: str | None) -> str:
    if not codigo:
        return ""
    return MOTIVOS_DE_RECUSA.get(codigo, codigo.replace("_", " ").capitalize())


def resumir_plano(plano: PlanoConsulta | None) -> list[tuple[str, str]]:
    """O plano da consulta como pares (rótulo, valor) legíveis, só com o que está preenchido."""
    if plano is None:
        return []
    linhas = [("Rota", plano.rota.value)]
    if plano.naturezas:
        linhas.append(("Tipos de evidência", ", ".join(n.value for n in plano.naturezas)))
    if plano.entidade:
        linhas.append(("Entidade", plano.entidade))
    if plano.filtros:
        linhas.append(("Filtros", "; ".join(f"{k} = {v}" for k, v in plano.filtros.items())))
    if plano.filtros_documentos:
        linhas.append(("Filtros dos documentos", "; ".join(f"{k} = {v}" for k, v in plano.filtros_documentos.items())))
    if plano.operacoes:
        linhas.append(("Operações", ", ".join(plano.operacoes)))
    if plano.campo_agregacao:
        linhas.append(("Campo", plano.campo_agregacao))
    if plano.agrupar_por:
        linhas.append(("Agrupar por", ", ".join(plano.agrupar_por)))
    if plano.limite:
        linhas.append(("Limite", str(plano.limite)))
    if plano.consulta_semantica:
        linhas.append(("Busca nos documentos", plano.consulta_semantica))
    return linhas


def linhas_de_fontes(resposta: RAGResponse) -> list[str]:
    return [f"[{f.numero}] {f.rotulo or f.chunk_id}" for f in sorted(resposta.sources_used, key=lambda f: f.numero)]
