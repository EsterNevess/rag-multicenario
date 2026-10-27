import pytest
from pydantic import ValidationError

from src.schema import (
    Fonte,
    Natureza,
    PlanoConsulta,
    RAGResponse,
    Rota,
)


def _fonte(numero: int = 1) -> Fonte:
    return Fonte(numero=numero, chunk_id=f"c{numero}", base_id="editais_normativo")


# ----------------------------------------------------------------- RAGResponse


def test_resposta_normal_com_fontes():
    resposta = RAGResponse(answer="Resposta [1].", sources_used=[_fonte()])
    assert not resposta.is_refusal


def test_recusa_nao_pode_citar_fontes():
    with pytest.raises(ValidationError, match="não pode citar fontes"):
        RAGResponse(
            answer="Não posso ajudar.",
            is_refusal=True,
            refusal_reason="prescricao_individual",
            sources_used=[_fonte()],
        )


def test_recusa_exige_motivo():
    with pytest.raises(ValidationError, match="refusal_reason"):
        RAGResponse(answer="Não posso ajudar.", is_refusal=True)


def test_motivo_sem_recusa_e_erro():
    with pytest.raises(ValidationError, match="só existe quando"):
        RAGResponse(answer="Ok", refusal_reason="x")


def test_numeros_de_fonte_nao_repetem():
    with pytest.raises(ValidationError, match="não podem se repetir"):
        RAGResponse(answer="Ok", sources_used=[_fonte(1), _fonte(1)])


# ----------------------------------------------------------------- PlanoConsulta


def test_plano_estruturado_valido():
    plano = PlanoConsulta(
        rota=Rota.ESTRUTURADA,
        naturezas=[Natureza.ESTRUTURADA],
        entidade="contratacao",
        filtros={"ano": 2026},
        operacoes=["contar", "agrupar"],
        agrupar_por=["modalidade"],
    )
    assert plano.limite is None


def test_rota_estruturada_exige_entidade():
    with pytest.raises(ValidationError, match="exige a entidade"):
        PlanoConsulta(rota=Rota.ESTRUTURADA, naturezas=[Natureza.ESTRUTURADA])


def test_rota_documentos_exige_consulta_semantica():
    with pytest.raises(ValidationError, match="consulta_semantica"):
        PlanoConsulta(rota=Rota.DOCUMENTOS, naturezas=[Natureza.NORMATIVA])


def test_hibrida_exige_duas_naturezas():
    with pytest.raises(ValidationError, match="duas naturezas"):
        PlanoConsulta(
            rota=Rota.HIBRIDA,
            naturezas=[Natureza.NORMATIVA],
            consulta_semantica="x",
        )


def test_agrupar_exige_campo_e_vice_versa():
    with pytest.raises(ValidationError, match="agrupar_por exige"):
        PlanoConsulta(
            rota=Rota.ESTRUTURADA,
            naturezas=[Natureza.ESTRUTURADA],
            entidade="contrato",
            agrupar_por=["modalidade"],
        )
    with pytest.raises(ValidationError, match="exige ao menos um campo"):
        PlanoConsulta(
            rota=Rota.ESTRUTURADA,
            naturezas=[Natureza.ESTRUTURADA],
            entidade="contrato",
            operacoes=["agrupar"],
        )


def test_soma_exige_campo_de_agregacao():
    with pytest.raises(ValidationError, match="campo_agregacao"):
        PlanoConsulta(
            rota=Rota.AGREGADA,
            naturezas=[Natureza.AGREGADA],
            entidade="contrato",
            operacoes=["somar"],
        )


def test_limite_fora_da_faixa_e_rejeitado():
    with pytest.raises(ValidationError):
        PlanoConsulta(
            rota=Rota.ESTRUTURADA,
            naturezas=[Natureza.ESTRUTURADA],
            entidade="contrato",
            limite=1000,
        )


def test_campo_desconhecido_e_rejeitado():
    """A LLM não pode inventar campos: extra='forbid'."""
    with pytest.raises(ValidationError):
        PlanoConsulta(
            rota=Rota.ESTRUTURADA,
            naturezas=[Natureza.ESTRUTURADA],
            entidade="contrato",
            sql="SELECT * FROM contrato",
        )
