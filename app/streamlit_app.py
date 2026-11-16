"""Interface de perguntas, com seletor de cenário.

Uso: streamlit run app/streamlit_app.py

Uma tela: escolha o cenário, faça a pergunta e veja a resposta com as fontes numeradas, a cobertura dos dados e, num painel à parte,
como o sistema decidiu (a rota e o plano da consulta). Não há login, histórico entre sessões nem memória de conversa: cada pergunta é independente.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from app import nucleo  # noqa: E402

st.set_page_config(page_title="RAG multicenário", page_icon="🔎", layout="centered")


@st.cache_resource(show_spinner="Carregando as bases do cenário...")
def contexto(cenario: str):
    return nucleo.criar_contexto(cenario)


def _mostrar(resultado: nucleo.Resultado) -> None:
    if resultado.erro:
        st.error(resultado.erro)
        return
    r = resultado.resposta
    if r.is_refusal:
        st.warning(r.answer)
        st.caption(f"Motivo: {nucleo.motivo_legivel(r.refusal_reason)}")
    else:
        st.markdown(r.answer)
    fontes = nucleo.linhas_de_fontes(r)
    if fontes:
        st.markdown("**Fontes**")
        for linha in fontes:
            st.markdown(f"- {linha}")
    if r.cobertura and not r.is_refusal:
        st.caption(f"Cobertura dos dados: {r.cobertura}")
    with st.expander("Como o sistema decidiu"):
        passos = nucleo.resumir_plano(r.plano)
        if passos:
            for rotulo, valor in passos:
                st.markdown(f"**{rotulo}:** {valor}")
            st.caption("O modelo só monta este plano; quem executa a consulta (e valida os campos) é o código. Ele nunca escreve SQL.")
        else:
            st.caption("A pergunta foi barrada antes de o modelo montar um plano (regra de entrada).")
        if resultado.usou_contexto:
            st.caption("Esta pergunta foi lida junto com a anterior (a pergunta e o plano dela). Se for um assunto novo, desmarque \"Continuar a conversa\" na lateral.")
        if r.trace_id:
            st.caption(f"Registro da consulta: {r.trace_id}")


def main() -> None:
    st.title("RAG multicenário")
    st.caption("Três cenários, um só motor: contratações públicas do RJ, saúde pública e pesquisa acadêmica.")

    with st.sidebar:
        cenario = st.selectbox("Cenário", list(nucleo.CENARIOS), format_func=nucleo.CENARIOS.get)
        st.markdown("**Antes de usar**")
        st.caption(
            "Os dados são um recorte datado. As atas, a ouvidoria e os pareceres são documentos **sintéticos**, gerados por script. "
            "Nada aqui substitui orientação clínica, jurídica ou profissional. As respostas citam as fontes; confira-as."
        )
        continuar = st.checkbox("Continuar a conversa", value=True, help="Perguntas como \"desses, quais...\" ou \"e em 2025?\" usam a pergunta anterior (só a pergunta e o plano dela, não a resposta). Memória de um turno.")
        st.caption("Limites conhecidos e medições: AVALIACAO.md no repositório.")

    if st.session_state.get("cenario_atual") != cenario:
        st.session_state["cenario_atual"] = cenario
        st.session_state["pergunta"] = ""
        st.session_state["resultado"] = None
        st.session_state["turno"] = None

    try:
        ctx = contexto(cenario)
    except Exception as erro:  # noqa: BLE001 - mostrar o motivo na tela é melhor que um traceback
        st.error(f"Não foi possível carregar o cenário: {erro}")
        st.stop()

    st.markdown("**Exemplos**")
    for i, exemplo in enumerate(nucleo.EXEMPLOS[cenario]):
        if st.button(exemplo, key=f"ex_{cenario}_{i}", use_container_width=True):
            st.session_state["pergunta"] = exemplo
            st.session_state["resultado"] = None

    pergunta = st.text_area("Pergunta", key="pergunta", height=90)
    if st.button("Perguntar", type="primary"):
        anterior = st.session_state.get("turno") if continuar else None
        with st.spinner("Consultando..."):
            resultado = nucleo.perguntar(ctx, pergunta, anterior)
        st.session_state["resultado"] = resultado
        st.session_state["turno"] = nucleo.turno_de(pergunta, resultado)

    if st.session_state.get("resultado") is not None:
        st.divider()
        _mostrar(st.session_state["resultado"])


main()
