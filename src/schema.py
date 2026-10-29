"""Modelos de dados do motor.

Tudo o que atravessa o pipeline (um trecho indexado, o plano da pergunta, a resposta)
tem uma forma fixa, validada. Se algo sai da forma, o erro aparece na hora e diz o motivo,
e não três etapas depois.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Natureza(str, Enum):
    """O tipo de evidência. O roteador decide por aqui, e não pelo assunto da pergunta."""

    NORMATIVA = "normativa"
    ESTRUTURADA = "estruturada"
    CONVERSACIONAL = "conversacional"
    AGREGADA = "agregada"


class Rota(str, Enum):
    ESTRUTURADA = "estruturada"
    AGREGADA = "agregada"
    DOCUMENTOS = "documentos"
    HIBRIDA = "hibrida"
    CATALOGO = "catalogo"
    FORA_DO_ESCOPO = "fora_do_escopo"


class Sensibilidade(str, Enum):
    PUBLICO = "publico"
    INTERNO = "interno"
    RESTRITO = "restrito"


# --------------------------------------------------------------------------- trechos


class ChunkMetadata(BaseModel):
    """Metadados obrigatórios de todo trecho indexado.

    Os cinco primeiros campos são o contrato mínimo: sem eles, o filtro por base, por
    sensibilidade ou por vigência não funciona e o erro só apareceria muito depois.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    source_file: str = Field(min_length=1)
    doc_type: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    sensitivity: Sensibilidade
    base_id: str = Field(min_length=1)

    natureza: Natureza | None = None
    data_publicacao: str | None = None
    data_vigencia_fim: str | None = None
    contem_dado_pessoal: bool = False
    sintetico: bool = False


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    texto: str = Field(min_length=1)
    metadata: ChunkMetadata


# --------------------------------------------------------------------------- plano


ValorFiltro = str | int | float | bool | None


class PlanoConsulta(BaseModel):
    """O que o analisador (a LLM) devolve. É só um plano: não executa nada.

    A LLM preenche estes campos; o planejador determinístico valida e executa. Ela nunca
    escreve SQL. A validação contra a tabela de capacidades de cada entidade (quais campos
    cada entidade tem) entra no passo do roteador.
    """

    model_config = ConfigDict(extra="forbid")

    rota: Rota
    naturezas: list[Natureza] = Field(default_factory=list)
    entidade: str | None = None
    filtros: dict[str, ValorFiltro] = Field(default_factory=dict)
    operacoes: list[Literal["listar", "contar", "somar", "media", "minimo", "maximo", "agrupar"]] = Field(
        default_factory=list
    )
    campo_agregacao: str | None = None
    agrupar_por: list[str] = Field(default_factory=list)
    limite: int | None = Field(default=None, ge=1, le=100)
    consulta_semantica: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _naturezas_nulas_viram_lista_vazia(cls, dados):
        if isinstance(dados, dict) and dados.get("naturezas") is None:
            dados = {**dados, "naturezas": []}
        return dados

    @model_validator(mode="after")
    def _coerente_com_a_rota(self) -> "PlanoConsulta":
        if self.rota not in (Rota.CATALOGO, Rota.FORA_DO_ESCOPO) and not self.naturezas:
            raise ValueError("informe ao menos uma natureza")
        if self.rota in (Rota.ESTRUTURADA, Rota.AGREGADA) and not self.entidade:
            raise ValueError(f"a rota {self.rota.value} exige a entidade consultada")
        if self.rota is Rota.DOCUMENTOS and not self.consulta_semantica:
            raise ValueError("a rota documentos exige consulta_semantica")
        if self.rota is Rota.HIBRIDA and len(set(self.naturezas)) < 2:
            raise ValueError("a rota hibrida exige pelo menos duas naturezas diferentes")
        if self.agrupar_por and "agrupar" not in self.operacoes:
            raise ValueError("agrupar_por exige a operação agrupar")
        if "agrupar" in self.operacoes and not self.agrupar_por:
            raise ValueError("a operação agrupar exige ao menos um campo em agrupar_por")
        somas = {"somar", "media", "minimo", "maximo"}
        if somas & set(self.operacoes) and not self.campo_agregacao:
            raise ValueError("somar, media, minimo e maximo exigem campo_agregacao")
        return self


# --------------------------------------------------------------------------- resposta


class Fonte(BaseModel):
    """Uma fonte citada na resposta. O número aparece no texto, por exemplo [1]."""

    model_config = ConfigDict(frozen=True)

    numero: int = Field(ge=1)
    chunk_id: str = Field(min_length=1)
    base_id: str = Field(min_length=1)
    rotulo: str | None = None


class RAGResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    sources_used: list[Fonte] = Field(default_factory=list)
    is_refusal: bool = False
    refusal_reason: str | None = None
    cobertura: str | None = None
    rotas: list[Rota] = Field(default_factory=list)
    trace_id: str | None = None

    @model_validator(mode="after")
    def _recusa_nao_cita_fontes(self) -> "RAGResponse":
        if self.is_refusal:
            if self.sources_used:
                raise ValueError("uma recusa não pode citar fontes")
            if not self.refusal_reason:
                raise ValueError("uma recusa precisa de refusal_reason")
        elif self.refusal_reason:
            raise ValueError("refusal_reason só existe quando is_refusal é verdadeiro")
        numeros = [f.numero for f in self.sources_used]
        if len(numeros) != len(set(numeros)):
            raise ValueError("os números das fontes não podem se repetir")
        return self
