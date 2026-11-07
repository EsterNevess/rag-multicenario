"""Testes da base agregada: indicadores calculados em SQL e conferidos contra valores feitos à mão."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest
import yaml

from src.capacidades import capacidades_do_cenario
from src.fetch_corpus import Pastas
from src.indicadores import IndicadoresInvalidos, construir, entidades_agregadas, interpretar_indicadores, ler_ficha, localizar_agregado
from src.normalize import COLUNAS_CONTRATACAO, COLUNAS_CONTRATO, _ddl
from src.router import PlanoInvalido, consultar, validar_plano
from src.schema import Natureza, PlanoConsulta, Rota

CAP = capacidades_do_cenario("rj")


@pytest.fixture()
def pastas(tmp_path) -> Pastas:
    dados = tmp_path / "data"
    (dados / "normalized" / "rj").mkdir(parents=True)
    c = sqlite3.connect(dados / "normalized" / "rj" / "v1.sqlite")
    c.execute(_ddl("contratacao", COLUNAS_CONTRATACAO))
    c.execute(_ddl("contrato", COLUNAS_CONTRATO))
    c.execute("CREATE TABLE metadados (chave TEXT PRIMARY KEY, valor TEXT)")

    def compra(n, ano, modalidade, estimado, data, esfera="E"):
        linha = dict.fromkeys(COLUNAS_CONTRATACAO)
        linha.update(numero_controle_pncp=f"1-1-{n}/{ano}", ano_compra=ano, modalidade_nome=modalidade, valor_total_estimado=estimado,
                     data_publicacao=data, esfera_id=esfera, uf_sigla="RJ")
        c.execute(f"INSERT INTO contratacao VALUES ({','.join('?' * len(COLUNAS_CONTRATACAO))})", [linha[k] for k in COLUNAS_CONTRATACAO])

    # 2026: 4 contratações, 3 diretas (2 dispensas + 1 inexigibilidade) e 1 pregão -> 0,75. 2025: 2 contratações, 1 direta -> 0,5.
    compra(1, 2026, "Dispensa", 100.0, "2026-01-10")
    compra(2, 2026, "Dispensa", 300.0, "2026-01-20", "F")
    compra(3, 2026, "Inexigibilidade", None, "2026-02-05")
    compra(4, 2026, "Pregão - Eletrônico", 1000.0, "2026-02-15")
    compra(5, 2025, "Dispensa", 50.0, "2025-12-01")
    compra(6, 2025, "Pregão - Eletrônico", 70.0, "2025-12-02")
    for n, (ano, valor) in enumerate([(2026, 1000.0), (2026, 3000.0), (2025, None)], 1):
        linha = dict.fromkeys(COLUNAS_CONTRATO)
        linha.update(numero_controle_pncp=f"2-2-{n}/{ano}", ano_contrato=ano, valor_global=valor, esfera_id="E", tipo_contrato_nome="Contrato", data_assinatura=f"{ano}-03-01")
        c.execute(f"INSERT INTO contrato VALUES ({','.join('?' * len(COLUNAS_CONTRATO))})", [linha[k] for k in COLUNAS_CONTRATO])
    c.execute("INSERT INTO metadados VALUES ('versao', 'v1'), ('cenario', 'rj'), ('relatorio', ?)",
              (json.dumps({"periodo_de_publicacao": {"de": "2025-12-01", "ate": "2026-02-15"}}),))
    c.commit()
    c.close()
    return Pastas(dados=dados, cenario=tmp_path / "sc", cenario_id="rj")


def _banco(pastas):
    construir("rj", "v1", pastas)
    return localizar_agregado(pastas, "v1")


def _linhas(banco, sql):
    c = sqlite3.connect(banco)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


# ----------------------------------------------------------------- valores conferidos à mão


def test_razao_grava_a_proporcao_em_valor_e_a_contagem_em_numerador_e_denominador(pastas):
    """Pegaria o erro em que as colunas do SELECT e da tabela estavam em ordens diferentes (valor recebia o numerador)."""
    banco = _banco(pastas)
    linhas = _linhas(banco, "SELECT ano_compra, valor, n, numerador, denominador FROM ind_proporcao_contratacao_direta ORDER BY ano_compra")
    assert linhas == [(2025, 0.5, 2, 1, 2), (2026, 0.75, 4, 3, 4)]


def test_contagem_soma_e_media_com_n_de_valores_considerados(pastas):
    banco = _banco(pastas)
    assert _linhas(banco, "SELECT mes_publicacao, valor, n FROM ind_contratacoes_por_mes ORDER BY 1") == [
        ("2025-12", 2.0, 2), ("2026-01", 2.0, 2), ("2026-02", 2.0, 2)]
    # a inexigibilidade sem valor estimado fica fora da soma, e n conta só quem tem valor
    assert _linhas(banco, "SELECT valor, n FROM ind_valor_estimado_por_modalidade WHERE ano_compra = 2026 AND modalidade_nome = 'Inexigibilidade'") == [(None, 0)]
    assert _linhas(banco, "SELECT valor, n FROM ind_valor_estimado_por_modalidade WHERE ano_compra = 2026 AND modalidade_nome = 'Dispensa'") == [(400.0, 2)]
    assert _linhas(banco, "SELECT ano_contrato, valor, n FROM ind_valor_medio_contrato_por_ano ORDER BY 1") == [(2025, None, 0), (2026, 2000.0, 2)]


def test_razao_por_esfera_usa_o_recorte_certo(pastas):
    banco = _banco(pastas)
    assert _linhas(banco, "SELECT esfera_id, valor, numerador, denominador FROM ind_proporcao_contratacao_direta_por_esfera WHERE ano_compra = 2026 ORDER BY 1") == [
        ("E", pytest.approx(2 / 3), 2, 3), ("F", 1.0, 1, 1)]


def test_a_definicao_de_contratacao_direta_vem_do_catalogo_e_mudar_a_definicao_muda_o_numero(pastas):
    """Mutação: se 'direta' deixar de incluir a inexigibilidade, a proporção de 2026 cai de 0,75 para 0,5."""
    dados = yaml.safe_load(open("scenarios/rj/indicadores.yaml", encoding="utf-8"))
    dados["indicadores"]["proporcao_contratacao_direta"]["numerador"] = [{"campo": "modalidade_nome", "em": ["Dispensa"]}]
    catalogo = interpretar_indicadores(dados, CAP)
    construir("rj", "v1", pastas, catalogo=catalogo)
    assert _linhas(localizar_agregado(pastas, "v1"), "SELECT valor FROM ind_proporcao_contratacao_direta WHERE ano_compra = 2026") == [(0.5,)]


def test_a_ficha_guarda_definicao_ressalvas_e_de_onde_veio(pastas):
    ficha = ler_ficha(_banco(pastas), "proporcao_contratacao_direta")
    assert "Dispensa" in ficha["definicao"] and "Inexigibilidade" in ficha["definicao"]
    assert any("Inaplicabilidade" in r for r in ficha["ressalvas"])
    assert ficha["dimensoes"] == ["ano_compra"] and ficha["unidade"] == "proporcao" and ficha["versao"] == "v1"
    assert ficha["calculado_sobre"]["periodo_de_publicacao"] == {"de": "2025-12-01", "ate": "2026-02-15"}
    assert len(ficha["calculado_sobre"]["sha256"]) == 64


def test_construir_de_novo_gera_o_mesmo_conteudo_e_nao_deixa_temporario(pastas):
    banco = _banco(pastas)
    antes = _linhas(banco, "SELECT * FROM ind_valor_estimado_por_modalidade ORDER BY 1, 2")
    construir("rj", "v1", pastas)
    assert _linhas(banco, "SELECT * FROM ind_valor_estimado_por_modalidade ORDER BY 1, 2") == antes
    assert not list((pastas.dados / "aggregated" / "rj").glob("*.tmp"))


def test_sem_banco_normalizado_diz_como_gerar(tmp_path):
    with pytest.raises(FileNotFoundError, match="src.normalize"):
        construir("rj", "v9", Pastas(dados=tmp_path / "d", cenario=tmp_path, cenario_id="rj"))


# ----------------------------------------------------------------- a rota agregada consulta a base pronta


def _plano(indicador, **kw):
    base = dict(rota=Rota.AGREGADA, naturezas=[Natureza.AGREGADA], entidade=indicador)
    base.update(kw)
    return PlanoConsulta(**base)


def test_consulta_de_indicador_filtra_por_dimensao_e_devolve_as_colunas_de_transparencia(pastas):
    banco = _banco(pastas)
    r = consultar(_plano("proporcao_contratacao_direta", filtros={"ano_compra": 2026}), banco, CAP)
    assert r.linhas == [{"ano_compra": 2026, "valor": 0.75, "n": 4, "numerador": 3, "denominador": 4}]


def test_valor_de_uma_razao_nao_pode_ser_somado_mas_o_de_uma_contagem_pode(pastas):
    ents = entidades_agregadas(CAP.catalogo_de_indicadores, CAP)
    assert not ents["proporcao_contratacao_direta"].campos["valor"].agregavel
    assert not ents["valor_medio_contrato_por_ano"].campos["valor"].agregavel
    assert ents["contratacoes_por_mes"].campos["valor"].agregavel and ents["valor_estimado_por_modalidade"].campos["valor"].agregavel
    with pytest.raises(PlanoInvalido, match="não dá para somar"):
        validar_plano(_plano("proporcao_contratacao_direta", operacoes=["somar"], campo_agregacao="valor"), CAP)


def test_soma_das_contagens_de_um_indicador_de_contagem_funciona(pastas):
    r = consultar(_plano("contratacoes_por_mes", operacoes=["somar"], campo_agregacao="valor"), _banco(pastas), CAP)
    assert r.linhas[0]["soma_valor"] == 6.0


def test_dimensao_que_o_indicador_nao_tem_e_recusada(pastas):
    with pytest.raises(PlanoInvalido, match="campo desconhecido"):
        validar_plano(_plano("proporcao_contratacao_direta", filtros={"modalidade_nome": "Dispensa"}), CAP)


# ----------------------------------------------------------------- validação do catálogo


def _base(**mudancas):
    ind = {"nome": "N", "descricao": "D", "unidade": "contagem", "origem": "contratacao", "dimensoes": ["ano_compra"], "metrica": "contagem", "definicao": "x"}
    ind.update(mudancas)
    return {"indicadores": {"i": ind}}


@pytest.mark.parametrize("mudanca,trecho", [
    ({"origem": "inexistente"}, "não existe"),
    ({"dimensoes": ["objeto_compra"]}, "não dá para agrupar"),
    ({"metrica": "mediana"}, "métrica"),
    ({"unidade": "furos"}, "unidade"),
    ({"metrica": "soma"}, "exige `campo`"),
    ({"metrica": "soma", "campo": "objeto_compra"}, "exige `campo`"),
    ({"metrica": "razao"}, "numerador"),
    ({"metrica": "razao", "numerador": [{"campo": "inventado", "igual": 1}]}, "campo desconhecido"),
    ({"metrica": "razao", "numerador": [{"campo": "modalidade_nome", "igual": 1, "em": ["a"]}]}, "exatamente um"),
    ({"filtro_fixo": [{"campo": "modalidade_nome", "em": []}]}, "não pode ser vazio"),
    ({"definicao": ""}, "falta `definicao`"),
    ({"palavras_chave": ["Contratação Direta"]}, "minúsculas e sem acento"),
    ({"palavras_chave": "contratacao direta"}, "lista de textos"),
    ({"exemplos": [{"pergunta": "x", "filtros": {"modalidade_nome": "Dispensa"}}]}, "não são dimensões"),
    ({"exemplos": [{"filtros": {}}]}, "precisa de `pergunta`"),
    ({"ressalvas": [{"o ano e o da compra": "ha casos de 2027"}]}, "ressalva precisa ser um texto"),
])
def test_catalogo_invalido_e_recusado_com_motivo(mudanca, trecho):
    with pytest.raises(IndicadoresInvalidos, match=trecho):
        interpretar_indicadores(_base(**mudanca), CAP)


def test_cada_indicador_declarado_so_usa_campos_que_existem_nos_dois_cenarios():
    for cenario in ("rj", "pesquisa"):
        cap = capacidades_do_cenario(cenario)
        assert cap.catalogo_de_indicadores.indicadores and cap.catalogo_de_indicadores.limites
        for ind in cap.catalogo_de_indicadores.indicadores.values():
            assert ind.definicao and ind.ressalvas, ind.id


# ----------------------------------------------------------------- de ponta a ponta, com o modelo falso


def _ctx(tmp_path, llm, banco_agregado):
    from src.generate import Contexto, listar_valores_existentes
    from src.obs import PipelineLogger

    ctx = Contexto(llm=llm, logger=PipelineLogger(tmp_path / "logs"), cap=CAP, banco_agregado=banco_agregado, hoje="2026-11-06", cobertura="jan a ago de 2026")
    ctx.valores_conhecidos = listar_valores_existentes(banco_agregado, CAP, entidades=CAP.entidades_agregadas())
    return ctx


def _plano_json(**kw):
    base = {"rota": "agregada", "naturezas": ["agregada"], "entidade": "proporcao_contratacao_direta", "filtros": {"ano_compra": 2026},
            "operacoes": [], "campo_agregacao": None, "agrupar_por": [], "limite": None, "consulta_semantica": None}
    base.update(kw)
    return json.dumps(base)


def test_rota_agregada_leva_definicao_recorte_e_ressalvas_ao_modelo_e_cita_o_indicador(tmp_path, pastas):
    from src.generate import responder
    from tests.fakes import LLMRoteirizado

    llm = LLMRoteirizado(_plano_json(), "Em 2026, 75% das contratações foram diretas [1].")
    r = responder("Qual a proporção de contratações diretas em 2026?", _ctx(tmp_path, llm, _banco(pastas)))

    assert not r.is_refusal and r.rotas == [Rota.AGREGADA]
    assert r.sources_used[0].base_id == "base_agregada"
    assert r.sources_used[0].rotulo == "Indicador: Proporção de contratações diretas (base agregada v1)"
    enviado = llm.chamadas[1][1]
    assert "Dispensa ou por Inexigibilidade" in enviado          # a definição
    assert "Inaplicabilidade" in enviado                         # a ressalva
    assert "ano_compra=2026; valor=0.75; n=4; numerador=3; denominador=4" in enviado
    assert "fração entre 0 e 1" in enviado                       # como ler a unidade
    assert "2025-12-01 a 2026-02-15" in enviado                  # o recorte da base


def test_o_analisador_recebe_o_catalogo_de_indicadores_e_o_que_nao_se_calcula(tmp_path, pastas):
    from src.generate import montar_prompt_analise

    prompt = montar_prompt_analise(_ctx(tmp_path, None, _banco(pastas)))
    assert '"agregada"' in prompt and "proporcao_contratacao_direta" in prompt and "valor_medio_contrato_por_ano" in prompt
    assert "Preço unitário ou por quilo" in prompt and "fora_do_escopo" in prompt
    assert "valores de modalidade_nome" in prompt and "Dispensa" in prompt   # os valores reais das dimensões


def test_sem_base_agregada_construida_a_rota_nem_aparece_no_prompt(tmp_path):
    from src.generate import Contexto, montar_prompt_analise
    from src.obs import PipelineLogger

    ctx = Contexto(llm=None, logger=PipelineLogger(tmp_path), cap=CAP, banco=tmp_path / "x")
    assert Natureza.AGREGADA not in ctx.naturezas and '"agregada"' not in montar_prompt_analise(ctx)


def test_indicador_usado_como_tabela_de_dados_e_recusado_e_volta_ao_modelo(tmp_path, pastas):
    from src.generate import responder
    from tests.fakes import LLMRoteirizado

    errado = _plano_json(rota="estruturada", naturezas=["estruturada"])
    llm = LLMRoteirizado(errado, _plano_json(), "75% [1].")
    ctx = _ctx(tmp_path, llm, _banco(pastas))
    ctx.banco = tmp_path / "x"  # há dados estruturados também; o plano errado não pode consultá-los com um indicador
    r = responder("Qual a proporção de contratações diretas em 2026?", ctx)
    assert not r.is_refusal and "entidade desconhecida" in llm.chamadas[1][1]


def test_valor_de_dimensao_que_nao_existe_no_indicador_volta_ao_modelo_com_a_lista(tmp_path, pastas):
    from src.generate import responder
    from tests.fakes import LLMRoteirizado

    ruim = _plano_json(entidade="valor_estimado_por_modalidade", filtros={"modalidade_nome": "Concurso"})
    bom = _plano_json(entidade="valor_estimado_por_modalidade", filtros={"modalidade_nome": "dispensa", "ano_compra": 2026})
    llm = LLMRoteirizado(ruim, bom, "Dispensas somam R$ 400 [1].")
    r = responder("Qual o valor estimado das dispensas?", _ctx(tmp_path, llm, _banco(pastas)))
    assert not r.is_refusal and "Inexigibilidade" in llm.chamadas[1][1]


def test_pergunta_que_o_catalogo_diz_nao_calcular_e_recusada_dizendo_o_porque(tmp_path, pastas):
    from src.generate import responder
    from tests.fakes import LLMRoteirizado

    llm = LLMRoteirizado(json.dumps({"rota": "fora_do_escopo", "naturezas": []}))
    r = responder("Qual o preço médio por quilo de arroz?", _ctx(tmp_path, llm, _banco(pastas)))
    assert r.is_refusal and r.refusal_reason == "fora_do_escopo"
    assert "Preço unitário ou por quilo" in r.answer and "não os itens" in r.answer.replace("\n", " ") or "itens" in r.answer
    assert len(llm.chamadas) == 1


def test_todo_indicador_dos_dois_cenarios_tem_exemplo_com_filtros_que_sao_dimensoes():
    for cenario in ("rj", "pesquisa"):
        for ind in capacidades_do_cenario(cenario).catalogo_de_indicadores.indicadores.values():
            assert ind.exemplos, (cenario, ind.id)
            for pergunta, filtros in ind.exemplos:
                assert pergunta.endswith("?") and set(filtros) <= set(ind.dimensoes), (ind.id, filtros)


def test_o_prompt_mostra_um_exemplo_por_indicador(tmp_path, pastas):
    from src.generate import montar_prompt_analise

    prompt = montar_prompt_analise(_ctx(tmp_path, None, _banco(pastas)))
    assert 'exemplo: "Que parte das compras do ano foi feita sem licitação' in prompt
    assert "entidade proporcao_contratacao_direta, filtros {\"ano_compra\": 2026}" in prompt
    assert prompt.count("exemplo:") == len(CAP.catalogo_de_indicadores.indicadores)


# ----------------------------------------------------------------- guarda de roteamento: se há indicador, o plano tem de usá-lo


def _ctx_com_dados(tmp_path, llm, pastas):
    ctx = _ctx(tmp_path, llm, _banco(pastas))
    ctx.banco = pastas.dados / "normalized" / "rj" / "v1.sqlite"  # há também a tabela de dados: o plano errado seria executável
    return ctx


def _plano_estruturado(**kw):
    base = {"rota": "estruturada", "naturezas": ["estruturada"], "entidade": "contratacao", "filtros": {"ano_compra": 2026}, "operacoes": ["contar"],
            "campo_agregacao": None, "agrupar_por": [], "limite": None, "consulta_semantica": None}
    base.update(kw)
    return json.dumps(base)


PERGUNTA = "Quantas contratações diretas houve em 2026 e qual a proporção sobre o total?"


def test_pergunta_que_pede_numero_e_cita_o_assunto_de_um_indicador_volta_ao_analisador(tmp_path, pastas):
    from src.generate import responder
    from tests.fakes import LLMRoteirizado

    llm = LLMRoteirizado(_plano_estruturado(), _plano_json(), "75% das contratações foram diretas [1].")
    r = responder(PERGUNTA, _ctx_com_dados(tmp_path, llm, pastas))
    assert not r.is_refusal and r.rotas == [Rota.AGREGADA]
    assert "existe um indicador pronto" in llm.chamadas[1][1] and "proporcao_contratacao_direta" in llm.chamadas[1][1]


def test_plano_que_ja_usa_o_indicador_passa_sem_nova_chamada(tmp_path, pastas):
    from src.generate import responder
    from tests.fakes import LLMRoteirizado

    llm = LLMRoteirizado(_plano_json(), "75% [1].")
    responder(PERGUNTA, _ctx_com_dados(tmp_path, llm, pastas))
    assert len(llm.chamadas) == 2  # analisador uma vez + geração


def test_pergunta_que_pede_registros_e_nao_um_numero_nao_e_desviada(tmp_path, pastas):
    from src.generate import responder
    from tests.fakes import LLMRoteirizado

    plano = _plano_estruturado(operacoes=["listar"], filtros={"modalidade_nome": "Dispensa"})
    llm = LLMRoteirizado(plano, "A contratação nº 1 [1].")
    r = responder("Quais contratações diretas por dispensa foram publicadas?", _ctx_com_dados(tmp_path, llm, pastas))  # "diretas" mas sem pedir número
    assert not r.is_refusal and r.rotas == [Rota.ESTRUTURADA] and len(llm.chamadas) == 2


def test_na_ultima_tentativa_o_plano_e_aceito_para_nao_travar_a_pergunta(tmp_path, pastas):
    from src.generate import responder
    from tests.fakes import LLMRoteirizado

    llm = LLMRoteirizado(_plano_estruturado(), _plano_estruturado(), _plano_estruturado(), "São 4 contratações [1].")
    r = responder(PERGUNTA, _ctx_com_dados(tmp_path, llm, pastas))
    assert not r.is_refusal and r.rotas == [Rota.ESTRUTURADA]  # o modelo insistiu três vezes; a guarda cedeu em vez de recusar


def test_sem_base_agregada_a_guarda_nao_age(tmp_path, pastas):
    from src.generate import Contexto, indicadores_sugeridos
    from src.obs import PipelineLogger

    ctx = Contexto(llm=None, logger=PipelineLogger(tmp_path), cap=CAP, banco=tmp_path / "x")
    assert indicadores_sugeridos(PERGUNTA, ctx) == []


@pytest.mark.parametrize("pergunta,esperado", [
    ("Quantas contratações diretas houve em 2026 e qual a proporção sobre o total?", {"proporcao_contratacao_direta", "proporcao_contratacao_direta_por_esfera"}),
    ("Qual o valor médio dos contratos em 2025?", {"valor_medio_contrato_por_ano"}),
    ("Quantas contratações foram publicadas em cada mês?", {"contratacoes_por_mes"}),
    ("Quais contratações diretas foram publicadas em 2026?", set()),                 # sem intenção de número
    ("Quantas contratações de material de escritório houve em 2026?", set()),        # nenhum indicador para esse assunto
    ("Qual o valor médio por quilo de arroz?", set()),                               # não é o valor médio dos contratos
])
def test_quais_indicadores_a_pergunta_sugere(tmp_path, pastas, pergunta, esperado):
    from src.generate import indicadores_sugeridos

    assert set(indicadores_sugeridos(pergunta, _ctx(tmp_path, None, _banco(pastas)))) == esperado


def test_a_mensagem_da_guarda_traz_um_plano_de_exemplo_pronto_do_indicador(tmp_path, pastas):
    from src.generate import responder
    from tests.fakes import LLMRoteirizado

    llm = LLMRoteirizado(_plano_estruturado(), _plano_json(), "75% [1].")
    responder(PERGUNTA, _ctx_com_dados(tmp_path, llm, pastas))
    mensagem = llm.chamadas[1][1]
    assert "Plano de exemplo" in mensagem and '"rota": "agregada"' in mensagem and '"entidade": "proporcao_contratacao_direta"' in mensagem


# ----------------------------------------------------------------- base desatualizada em relação ao catálogo


def test_base_agregada_construida_com_o_catalogo_atual_passa_na_conferencia(pastas):
    from src.indicadores import conferir_atualizacao

    conferir_atualizacao(_banco(pastas), "rj")


def test_base_agregada_de_outra_versao_do_catalogo_e_recusada_com_o_comando_para_refazer(pastas, tmp_path):
    import shutil

    from src.indicadores import BaseDesatualizada, conferir_atualizacao

    banco = _banco(pastas)
    raiz = tmp_path / "raiz"
    (raiz / "scenarios" / "rj").mkdir(parents=True)
    shutil.copy("scenarios/rj/indicadores.yaml", raiz / "scenarios" / "rj" / "indicadores.yaml")
    conferir_atualizacao(banco, "rj", raiz)  # a cópia idêntica passa
    arquivo = raiz / "scenarios" / "rj" / "indicadores.yaml"
    arquivo.write_text(arquivo.read_text(encoding="utf-8").replace("Lei 14.133/2021, arts. 74 e 75", "Lei 14.133/2021"), encoding="utf-8")
    with pytest.raises(BaseDesatualizada, match="python -m src.indicadores build --scenario rj"):
        conferir_atualizacao(banco, "rj", raiz)


def test_quebra_de_linha_nao_conta_como_mudanca_do_catalogo(pastas, tmp_path):
    from src.indicadores import conferir_atualizacao

    banco = _banco(pastas)
    raiz = tmp_path / "raiz"
    (raiz / "scenarios" / "rj").mkdir(parents=True)
    texto = open("scenarios/rj/indicadores.yaml", "rb").read().replace(b"\r\n", b"\n")
    (raiz / "scenarios" / "rj" / "indicadores.yaml").write_bytes(texto.replace(b"\n", b"\r\n"))
    conferir_atualizacao(banco, "rj", raiz)
