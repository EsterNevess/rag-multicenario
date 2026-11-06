import pytest

from src.pii import (
    MARCA_CPF,
    MARCA_EMAIL,
    MARCA_TELEFONE,
    contem_dado_pessoal,
    cpf_valido,
    mascarar_dados_pessoais,
)


def test_mascara_cpf_com_e_sem_pontuacao():
    assert mascarar_dados_pessoais("CPF 123.456.789-09") == f"CPF {MARCA_CPF}"
    assert mascarar_dados_pessoais("CPF 12345678909") == f"CPF {MARCA_CPF}"


def test_mascara_email_e_telefone():
    texto = "Fale com maria.silva@exemplo.com ou (21) 98765-4321."
    mascarado = mascarar_dados_pessoais(texto)
    assert MARCA_EMAIL in mascarado
    assert MARCA_TELEFONE in mascarado
    assert "maria.silva" not in mascarado
    assert "98765" not in mascarado


@pytest.mark.parametrize(
    "texto",
    [
        "Período 2025-2026",
        "Edital 1234-5678 de 2026",
        "Processo 2026-0001",
        "Portaria 4567-8901",
        "Contrato 00394502000144-2-002024/2025",
    ],
)
def test_numeros_com_hifen_do_dominio_nao_viram_telefone(texto):
    assert mascarar_dados_pessoais(texto) == texto


@pytest.mark.parametrize(
    "texto",
    [
        "(21) 2222-3333",
        "(21)98765-4321",
        "celular 98765-4321",
        "Tel: 3333-4444",
        "WhatsApp 3333-4444",
        "contato: 3333-4444",
    ],
)
def test_telefones_com_sinal_claro_sao_mascarados(texto):
    assert MARCA_TELEFONE in mascarar_dados_pessoais(texto)


def test_cpf_formatado_e_sempre_mascarado_mesmo_sem_digito_verificador_valido():
    assert mascarar_dados_pessoais("CPF 123.456.789-00") == f"CPF {MARCA_CPF}"


def test_codigo_de_produto_com_11_digitos_nao_e_cpf():
    # Códigos reais de uma tabela de edital de equipamentos: os dígitos verificadores não fecham.
    for codigo in ("66410570001", "41100060020", "66500110081"):
        assert mascarar_dados_pessoais(f"item {codigo} fornecedor") == f"item {codigo} fornecedor"


def test_cpf_sem_pontuacao_so_com_digito_verificador_valido():
    assert cpf_valido("12345678909")
    assert not cpf_valido("12345678900")
    assert not cpf_valido("11111111111")  # sequência repetida passa na conta, mas não existe
    assert mascarar_dados_pessoais("11111111111") == "11111111111"


def test_email_funcional_de_orgao_publico_e_mantido():
    texto = "impugnações para licitacao@saude.rj.gov.br até 3 dias úteis"
    assert mascarar_dados_pessoais(texto) == texto


def test_email_de_provedor_comum_e_mascarado():
    assert mascarar_dados_pessoais("fale com joao@gmail.com") == f"fale com {MARCA_EMAIL}"
    assert mascarar_dados_pessoais("empresa@fornecedor.com.br") == MARCA_EMAIL


def test_cnpj_nao_e_alterado():
    cnpj = "O fornecedor, CNPJ 42.498.733/0001-48, venceu o pregão."
    assert mascarar_dados_pessoais(cnpj) == cnpj


def test_numero_de_controle_do_pncp_nao_e_alterado():
    texto = "Contrato 42498733000148-2-000841/2026 assinado em 2026."
    assert mascarar_dados_pessoais(texto) == texto


def test_valor_em_reais_nao_e_alterado():
    texto = "Valor global de R$ 1.234.567,89 em 2026."
    assert mascarar_dados_pessoais(texto) == texto


def test_contem_dado_pessoal():
    assert contem_dado_pessoal("meu e-mail é a@b.com")
    assert not contem_dado_pessoal("Quantas contratações em 2026?")


def test_dominio_institucional_declarado_pelo_corpus_mantem_o_email():
    texto = "Dúvidas: atendimento@faperj.br ou joao@gmail.com ou x@sub.cnpq.br"
    mascarado = mascarar_dados_pessoais(texto, ("faperj.br", "cnpq.br"))
    assert "atendimento@faperj.br" in mascarado and "x@sub.cnpq.br" in mascarado
    assert "joao@gmail.com" not in mascarado
    assert "atendimento@faperj.br" not in mascarar_dados_pessoais(texto)  # sem declarar, continua mascarando


def test_dominio_declarado_nao_libera_dominio_parecido():
    assert "faperj.br" not in mascarar_dados_pessoais("a@naofaperj.br b@faperj.br.falso.com", ("faperj.br",))


# ----------------------------------------------------------------- credencial e dado bancário colados no texto


@pytest.mark.parametrize("texto,visivel", [
    ("Segue o acesso. A senha do sistema é Abc123!x e vale até sexta.", "A senha do sistema é [CREDENCIAL]"),
    ("login: joao / senha: 12345678", "senha: [CREDENCIAL]"),
    ("Token de acesso = ghp_abcdef123456 não compartilhe", "Token de acesso = [CREDENCIAL]"),
    ("a chave de acesso é XYZ-99", "chave de acesso é [CREDENCIAL]"),
    ("API key: sk-teste-000", "API key: [CREDENCIAL]"),
])
def test_credencial_colada_no_texto_e_mascarada(texto, visivel):
    saida = mascarar_dados_pessoais(texto)
    assert visivel in saida
    for segredo in ("Abc123!x", "12345678", "ghp_abcdef123456", "XYZ-99", "sk-teste-000"):
        assert segredo not in saida


@pytest.mark.parametrize("texto", [
    "Depositar na agência 1234-5, conta corrente 56789-0, em nome da empresa.",
    "Ag. 0001 c/c 123456-7",
    "Chave Pix: 12.345.678/0001-90",
])
def test_dado_bancario_e_mascarado(texto):
    saida = mascarar_dados_pessoais(texto)
    assert "[DADO BANCÁRIO]" in saida and "56789" not in saida and "123456-7" not in saida and "0001-90" not in saida


@pytest.mark.parametrize("texto", [
    "A senha de acesso ao SEI deve ser solicitada ao suporte.",       # fala da senha, não traz uma
    "Troque a senha periodicamente.",
    "O agente de contratação informou o prazo de 2026.",
    "O token é um conceito de segurança.",
])
def test_menção_a_senha_sem_valor_nao_e_alterada(texto):
    assert mascarar_dados_pessoais(texto) == texto
