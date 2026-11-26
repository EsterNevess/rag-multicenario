"""Comparação de versões de PCDT, seção por seção."""

from __future__ import annotations

import pytest

from src.pcdt_versoes import Secao, casar_secoes, chave_do_titulo, comparar_versoes, frases, secoes_da_versao, tabela_de_secoes

FRASE_A = "O metotrexato em monoterapia deve ser a primeira escolha terapêutica no tratamento inicial."
FRASE_B = "Os pacientes devem ser informados sobre os riscos, benefícios e eventos adversos do tratamento."
FRASE_C = "A prescrição de medicamentos biológicos dependerá da disponibilidade na assistência farmacêutica."
FRASE_D = "Foram incorporadas as deliberações recentes da Conitec sobre os inibidores da Janus quinase."


def _chunk(rotulo, texto, versao="V1", doenca="Artrite", data="2020-01-01"):
    return {"metadata": {"doenca": doenca, "versao": versao, "data_publicacao": data, "rotulo": rotulo}, "texto": f"PCDT - {rotulo}\n{texto}"}


def test_chave_do_titulo_ignora_numeracao_acento_caixa_e_pontuacao():
    assert chave_do_titulo("7    FÁRMACOS") == chave_do_titulo("8. Fármacos") == "farmacos"
    assert chave_do_titulo("10.   TERMO DE ESCLARECIMENTO E RESPONSABILIDADE (TER)") == "termo de esclarecimento e responsabilidade ter"
    assert chave_do_titulo("Preâmbulo (parte 1/3)") != ""


def test_itens_entram_na_secao_que_os_precede_e_partes_do_mesmo_titulo_se_juntam():
    secoes, ignoradas = secoes_da_versao([
        _chunk("6.   TRATAMENTO (parte 1/2)", "texto a"), _chunk("6.   TRATAMENTO (parte 2/2)", "texto b"),
        _chunk("Item 6.1", "item um"), _chunk("7.   MONITORAMENTO", "monitorar"),
    ])
    assert [s.chave for s in secoes] == ["tratamento", "monitoramento"] and ignoradas == 0
    assert secoes[0].texto == "texto a\ntexto b\nitem um"


def test_titulo_repetido_de_anexo_do_mesmo_pdf_e_ignorado_e_contado():
    secoes, ignoradas = secoes_da_versao([
        _chunk("1.   INTRODUÇÃO", "introdução do protocolo"), _chunk("2.   TRATAMENTO", "tratamento do protocolo"),
        _chunk("1.   INTRODUÇÃO", "introdução do anexo"), _chunk("Item 1.1", "item do anexo"),
    ])
    assert [s.chave for s in secoes] == ["introducao", "tratamento"]
    assert "anexo" not in secoes[0].texto and "anexo" not in secoes[1].texto and ignoradas == 1


def test_casar_secoes_por_titulo_igual_por_prefixo_e_por_semelhanca_e_lista_as_sem_par():
    nova = [Secao("5. DIAGNÓSTICO E CLASSIFICAÇÃO", chave_do_titulo("5. DIAGNÓSTICO E CLASSIFICAÇÃO")),
            Secao("8. REGULAÇÃO, CONTROLE EAVALIAÇÃO PELO GESTOR", chave_do_titulo("8. REGULAÇÃO, CONTROLE EAVALIAÇÃO PELO GESTOR")),
            Secao("6. TRATAMENTO", chave_do_titulo("6. TRATAMENTO")), Secao("9. SÓ NA NOVA", chave_do_titulo("9. SÓ NA NOVA"))]
    antiga = [Secao("5. DIAGNÓSTICO E CLASSIFICAÇÃO DE ARTRITE REUMATOIDE", chave_do_titulo("5. DIAGNÓSTICO E CLASSIFICAÇÃO DE ARTRITE REUMATOIDE")),
              Secao("10. REGULAÇÃO/CONTROLE/AVALIAÇÃO PELO GESTOR", chave_do_titulo("10. REGULAÇÃO/CONTROLE/AVALIAÇÃO PELO GESTOR")),
              Secao("6    TRATAMENTO", chave_do_titulo("6    TRATAMENTO")), Secao("7 SÓ NA ANTIGA", chave_do_titulo("7 SÓ NA ANTIGA"))]
    pares = [(n.chave if n else None, a.chave if a else None) for n, a in casar_secoes(nova, antiga)]
    assert pares[0][1] == "diagnostico e classificacao de artrite reumatoide"   # um título contém o outro
    assert pares[1][1] == chave_do_titulo("REGULAÇÃO/CONTROLE/AVALIAÇÃO PELO GESTOR")  # pontuação e erro de digitação
    assert pares[2][1] == "tratamento"
    assert pares[3] == ("so na nova", None) and pares[4] == (None, "so na antiga")


def test_frases_descarta_pedacos_curtos_e_quebra_em_ponto_final_e_nova_linha():
    assert frases(f"{FRASE_A} {FRASE_B}\nCurta.\n{FRASE_C}") == [FRASE_A, FRASE_B, FRASE_C]


def test_secao_igual_nas_duas_versoes_tem_100_por_cento_mantido_mesmo_com_trechos_cortados_de_outro_jeito():
    nova = [_chunk("1.   INTRODUÇÃO", f"{FRASE_A} {FRASE_B}", versao="V2")]
    anterior = [_chunk("1.   INTRODUÇÃO (parte 1/2)", FRASE_A, versao="V1"), _chunk("1.   INTRODUÇÃO (parte 2/2)", FRASE_B, versao="V1")]
    linhas, _ = comparar_versoes(nova, anterior)
    assert len(linhas) == 1 and linhas[0]["presenca"] == "nas duas versões"
    assert linhas[0]["frases_novas"] == 0 and linhas[0]["frases_removidas"] == 0 and linhas[0]["frases_mantidas_pct"] == 100.0


def test_frase_nova_e_frase_removida_sao_contadas_e_a_nova_vira_exemplo():
    nova = [_chunk("1.   INTRODUÇÃO", f"{FRASE_A} {FRASE_D}", versao="V2")]
    anterior = [_chunk("1.   INTRODUÇÃO", f"{FRASE_A} {FRASE_C}", versao="V1")]
    (linha,), _ = comparar_versoes(nova, anterior)
    assert linha["frases_novas"] == 1 and linha["frases_removidas"] == 1 and linha["frases_mantidas_pct"] == 50.0
    assert linha["exemplos_de_frases_novas"] == FRASE_D


def test_secao_so_numa_das_versoes_nao_inventa_contagem_de_frases():
    nova = [_chunk("1.   INTRODUÇÃO", FRASE_A, versao="V2"), _chunk("2.   ESQUEMAS", FRASE_B, versao="V2")]
    anterior = [_chunk("1.   INTRODUÇÃO", FRASE_A, versao="V1"), _chunk("3.   CASOS ESPECIAIS", FRASE_C, versao="V1")]
    linhas, _ = comparar_versoes(nova, anterior)
    por_presenca = {l["presenca"]: l for l in linhas}
    assert set(por_presenca) == {"nas duas versões", "só na versão nova", "só na versão anterior"}
    assert por_presenca["só na versão nova"]["frases_novas"] is None and por_presenca["só na versão anterior"]["caracteres_nova"] is None


def test_tabela_compara_versoes_consecutivas_por_data_e_avisa_doenca_de_versao_unica():
    chunks = [
        _chunk("1.   INTRODUÇÃO", FRASE_A, versao="V2019", data="2019-01-01"),
        _chunk("1.   INTRODUÇÃO", f"{FRASE_A} {FRASE_B}", versao="V2026", data="2026-01-01"),
        _chunk("1.   INTRODUÇÃO", FRASE_C, versao="V2021", data="2021-01-01"),
        _chunk("1.   INTRODUÇÃO", FRASE_A, versao="VP", doenca="Parkinson", data="2017-01-01"),
    ]
    linhas, rel = tabela_de_secoes(chunks)
    assert [(l["versao_anterior"], l["versao_nova"]) for l in linhas] == [("V2019", "V2021"), ("V2021", "V2026")]
    assert [l["id"] for l in linhas] == [1, 2] and rel["doencas_com_uma_so_versao"] == ["Parkinson"]


def test_regressao_do_sa02_assunto_presente_nas_duas_versoes_nao_aparece_como_acrescentado():
    """O modelo disse que o Termo de Esclarecimento 'não aparece' na versão anterior porque o trecho recuperado não o continha."""
    termo = "O termo de esclarecimento e responsabilidade deve ser assinado pelo paciente antes do início do tratamento."
    nova = [_chunk("9.   TERMO DE ESCLARECIMENTO E RESPONSABILIDADE – TER", termo, versao="V2026", data="2026-01-01")]
    anterior = [_chunk("10.   TERMO DE ESCLARECIMENTO E RESPONSABILIDADE (TER)", termo, versao="V2021", data="2021-01-01")]
    (linha,), _ = comparar_versoes(nova, anterior)
    assert linha["presenca"] == "nas duas versões" and linha["frases_novas"] == 0


# ----------------------------------------------------------------- conferência à mão das versões da artrite (16/11)


def test_fragmento_de_tabela_nao_e_titulo_e_o_texto_continua_a_secao_em_andamento():
    from src.pcdt_versoes import titulo_valido

    assert not titulo_valido("46 - A média do DM 0,19 menor") and not titulo_valido("46 - A média do índice DM0,19 maior")
    assert not titulo_valido("46 - A MÉDIA DO DM 0,19 MENOR")  # em caixa alta: só a regra do fragmento de tabela pega
    assert not titulo_valido("Resultado da análise feita pelo grupo com dados de 2019")  # texto corrido: quase sem maiúsculas
    assert titulo_valido("7    FÁRMACOS") and titulo_valido("Preâmbulo") and titulo_valido("2. CLASSIFICAÇÃO ESTATÍSTICA INTERNACIONAL DE DOENÇAS")
    secoes, _ = secoes_da_versao([
        _chunk("6.   TRATAMENTO", "texto do tratamento"), _chunk("46 - A média do DM 0,19 menor", "linha de tabela"), _chunk("7.   MONITORAMENTO", "monitorar"),
    ])
    assert [s.chave for s in secoes] == ["tratamento", "monitoramento"] and "linha de tabela" in secoes[0].texto


def test_titulo_exato_ganha_do_titulo_curto_que_so_e_prefixo():
    """Antes, '5. RECOMENDAÇÕES' (um título solto, de 16 caracteres) roubava o par de '5 RECOMENDAÇÕES PARA PESQUISA' antes do exato."""
    nova = [Secao("5. RECOMENDAÇÕES", chave_do_titulo("5. RECOMENDAÇÕES")), Secao("6 . RECOMENDAÇÕES PARA PESQUISA", chave_do_titulo("6 . RECOMENDAÇÕES PARA PESQUISA"))]
    antiga = [Secao("5 RECOMENDAÇÕES PARA PESQUISA", chave_do_titulo("5 RECOMENDAÇÕES PARA PESQUISA"))]
    pares = [(n.titulo, a.titulo if a else None) for n, a in casar_secoes(nova, antiga) if n]
    assert pares == [("5. RECOMENDAÇÕES", None), ("6 . RECOMENDAÇÕES PARA PESQUISA", "5 RECOMENDAÇÕES PARA PESQUISA")]


@pytest.mark.parametrize("nova,antiga", [
    ("10. MONITORIZAÇÃO", "9 MONITORAMENTO"),
    ("6. ABORDAGEM TERAPÊUTICA", "8 TRATAMENTO"),
    ("3. CRITÉRIOS DE ELEGIBILIDADE", "5 CRITÉRIOS DE INCLUSÃO"),
    ("11. GESTÃO E CONTROLE", "11 REGULAÇÃO/CONTROLE/AVALIAÇÃO PELO GESTOR"),
    ("8. FÁRMACOS", "8. FÁRMACOS E ESQUEMA DE ADMINISTRAÇÃO"),  # um título contém o outro, a partir de 8 caracteres
])
def test_titulos_que_protocolos_de_epocas_diferentes_dao_a_mesma_parte_casam(nova, antiga):
    pares = casar_secoes([Secao(nova, chave_do_titulo(nova))], [Secao(antiga, chave_do_titulo(antiga))])
    assert pares[0][1] is not None and pares[0][1].titulo == antiga


def test_titulos_de_partes_diferentes_nao_casam_so_por_causa_dos_sinonimos():
    pares = casar_secoes([Secao("7. FLUXOGRAMA DE TRATAMENTO", chave_do_titulo("7. FLUXOGRAMA DE TRATAMENTO"))], [Secao("8 TRATAMENTO", chave_do_titulo("8 TRATAMENTO"))])
    assert pares[0][1] is None and pares[1] == (None, pares[1][1])


def test_numeracao_que_recomeca_marca_outro_documento_do_mesmo_pdf():
    secoes, _ = secoes_da_versao([
        _chunk("Preâmbulo", "capa"), _chunk("1.   INTRODUÇÃO", "intro"), _chunk("2.   TRATAMENTO", "trat"), _chunk("10.   REGULAÇÃO", "reg"),
        _chunk("3.   DIAGNÓSTICO", "diag da AIJ"), _chunk("7.   CASOS ESPECIAIS", "casos"),
        _chunk("1.   APRESENTAÇÃO", "apêndice"),
    ])
    assert {s.chave: s.parte_do_pdf for s in secoes} == {
        "preambulo": 1, "introducao": 1, "tratamento": 1, "regulacao": 1, "diagnostico": 2, "casos especiais": 2, "apresentacao": 3}


def test_pdf_de_um_documento_so_fica_todo_na_parte_principal():
    secoes, _ = secoes_da_versao([_chunk(f"{n}.   SEÇÃO {chr(64 + n)}", "texto") for n in range(1, 8)])
    assert {s.parte_do_pdf for s in secoes} == {1}


def test_tabela_distingue_o_protocolo_de_outro_documento_do_mesmo_pdf():
    anterior = [_chunk("5.   DIAGNÓSTICO E CLASSIFICAÇÃO", FRASE_A), _chunk("6.   TRATAMENTO", FRASE_B), _chunk("3.   DIAGNÓSTICO", FRASE_C)]  # a 3 volta a numeração: outro documento
    nova = [_chunk("5.   DIAGNÓSTICO E CLASSIFICAÇÃO", FRASE_A), _chunk("6.   TRATAMENTO", FRASE_B)]
    linhas, _ = comparar_versoes(nova, anterior)
    por_secao = {l["secao"]: l for l in linhas}
    assert por_secao["3.   DIAGNÓSTICO"]["presenca"] == "só na versão anterior"
    assert por_secao["3.   DIAGNÓSTICO"]["parte_do_pdf"] == "outro documento do mesmo PDF"
    assert por_secao["6.   TRATAMENTO"]["parte_do_pdf"] == "protocolo"
