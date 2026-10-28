"""Roteador: o que entra, o que pode ser pedido e como a base estruturada é consultada.

Três proteções ficam aqui, todas determinísticas (sem LLM):

1. **Guardrail de entrada** (`avaliar_pergunta`): roda antes do analisador e recusa o que o projeto
   decidiu não fazer (ver `sensibilidade.yaml`), sem gastar uma chamada ao modelo.
2. **Validação do plano** (`validar_plano`): o plano da LLM só vale se usar entidades, campos e
   operações que a tabela de capacidades declara. Nome inventado vira erro, não consulta.
3. **Consulta controlada** (`consultar`): o SQL é montado aqui, a partir de nomes da tabela de
   capacidades (nunca texto da LLM) e de valores sempre passados como parâmetro, sobre o banco
   aberto só para leitura. A LLM nunca escreve SQL.
"""

from __future__ import annotations

import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.fetch_corpus import Pastas, versoes_existentes
from src.schema import Natureza, PlanoConsulta, Rota

TAMANHO_MAXIMO_PERGUNTA = 1000
LIMITE_PADRAO = 20
TEMPO_MAXIMO_CONSULTA_S = 10.0

# --------------------------------------------------------------------------- guardrail de entrada


@dataclass(frozen=True)
class Decisao:
    permitida: bool
    categoria: str | None = None
    mensagem: str | None = None


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower()


# (categoria, mensagem, padrões sobre o texto sem acento e em minúsculas)
REGRAS_DE_ENTRADA: list[tuple[str, str, list[str]]] = [
    (
        "injecao_de_prompt",
        "Não posso seguir instruções que tentam mudar as regras do sistema. Posso responder perguntas sobre contratações públicas do RJ.",
        [
            r"ignor\w* (todas? )?(as |suas |tuas )?(instruco\w*|regras|orientac\w*)",
            r"ignore (all |any |the )?(previous|prior|above) (instructions|rules)",
            r"(revele|mostre|exiba|repita|imprima) ((o|seu|teu) )*(system )?prompt",
            r"(system|developer) prompt|prompt de sistema",
            r"voce (agora )?(e|sera) (um|uma|o|a) ",
            r"finja (que )?(voce )?(e|nao tem)",
        ],
    ),
    (
        "comando_sql",
        "Não executo comandos de banco de dados. Faça a pergunta em linguagem natural que eu consulto os dados com segurança.",
        [
            r"\b(drop|truncate|alter)\s+(table|database|index)\b",
            r"\b(delete\s+from|insert\s+into|update\s+\w+\s+set)\b",
            r"\bselect\b.+\bfrom\b",
            r";\s*--",
            r"\bunion\s+select\b",
        ],
    ),
    (
        "dado_bancario_ou_credencial",
        "Não informo dados bancários de fornecedores nem credenciais ou senhas.",
        [
            r"\b(conta bancaria|numero da conta|agencia bancaria|dados bancarios|chave pix|\bpix\b)",
            r"\b(senha|password|token de acesso|chave de api|api key|credenciais?)\b",
        ],
    ),
    (
        "juizo_de_legalidade",
        "Não faço juízo sobre a legalidade de uma contratação específica nem acuso ninguém. Posso apresentar os dados e o que a lei diz, para você avaliar.",
        [
            r"\b(foi|e|houve|ha|existe|teve) (uma? )?(fraude|corrupcao|superfaturamento|ilegal|ilegalidade|crime|desvio)",
            r"\b(confirm\w+|prov\w+|comprov\w+) (a |que (foi |houve )?)?(fraude|corrupcao|superfaturamento|desvio|crime)",
        ],
    ),
    (
        "dado_pessoal",
        "Não consulto nem informo CPF ou dados pessoais de pessoas físicas. Posso responder sobre órgãos, empresas (CNPJ) e valores.",
        [
            r"\bcpf\b",
            r"(telefone|celular|whatsapp|endereco residencial|e-?mail pessoal)\s+(d[eoa]s?\s+)?(servidor|fiscal|pregoeir|pessoa|fulano|ciclano)",
        ],
    ),
]
_REGRAS_COMPILADAS = [(c, m, [re.compile(p) for p in ps]) for c, m, ps in REGRAS_DE_ENTRADA]


def avaliar_pergunta(pergunta: str) -> Decisao:
    """Primeira barreira: decide se a pergunta pode seguir para o analisador."""
    if not pergunta or not pergunta.strip():
        return Decisao(False, "pergunta_vazia", "Escreva uma pergunta sobre contratações públicas do RJ.")
    if len(pergunta) > TAMANHO_MAXIMO_PERGUNTA:
        return Decisao(False, "pergunta_longa", f"A pergunta passa de {TAMANHO_MAXIMO_PERGUNTA} caracteres. Resuma em uma ou duas frases.")
    texto = _sem_acento(unicodedata.normalize("NFKC", pergunta))
    for categoria, mensagem, padroes in _REGRAS_COMPILADAS:
        if any(p.search(texto) for p in padroes):
            return Decisao(False, categoria, mensagem)
    return Decisao(True)


# --------------------------------------------------------------------------- tabela de capacidades


@dataclass(frozen=True)
class Campo:
    nome: str
    tipo: str  # texto | categoria | inteiro | valor | data | id
    filtravel: bool = True
    agregavel: bool = False  # aceita soma, média, mínimo e máximo
    agrupavel: bool = False
    expr: str | None = None  # expressão SQL quando o campo é calculado (nunca vem do plano)

    @property
    def sql(self) -> str:
        return self.expr or self.nome


@dataclass(frozen=True)
class Entidade:
    tabela: str
    campos: dict[str, Campo]
    exibir: tuple[str, ...]
    ordem_padrao: str


def _campos(*cs: Campo) -> dict[str, Campo]:
    return {c.nome: c for c in cs}


ENTIDADES: dict[str, Entidade] = {
    "contratacao": Entidade(
        tabela="contratacao",
        exibir=("numero_controle_pncp", "orgao_razao_social", "objeto_compra", "modalidade_nome",
                "valor_total_estimado", "valor_total_homologado", "data_publicacao"),
        ordem_padrao="data_publicacao",
        campos=_campos(
            Campo("numero_controle_pncp", "id"),
            Campo("orgao_cnpj", "id", agrupavel=True),
            Campo("orgao_razao_social", "categoria", agrupavel=True),
            Campo("esfera_id", "categoria", agrupavel=True),
            Campo("uf_sigla", "categoria", agrupavel=True),
            Campo("municipio_nome", "categoria", agrupavel=True),
            Campo("ano_compra", "inteiro", agrupavel=True),
            Campo("modalidade_nome", "categoria", agrupavel=True),
            Campo("situacao_nome", "categoria", agrupavel=True),
            Campo("srp", "inteiro", agrupavel=True),
            Campo("objeto_compra", "texto"),
            Campo("amparo_legal_nome", "categoria", agrupavel=True),
            Campo("valor_total_estimado", "valor", agregavel=True),
            Campo("valor_total_homologado", "valor", agregavel=True),
            Campo("data_publicacao", "data"),
            Campo("mes_publicacao", "texto", filtravel=False, agrupavel=True, expr="substr(data_publicacao, 1, 7)"),
        ),
    ),
    "contrato": Entidade(
        tabela="contrato",
        exibir=("numero_controle_pncp", "orgao_razao_social", "nome_fornecedor", "objeto_contrato",
                "valor_global", "data_assinatura"),
        ordem_padrao="data_assinatura",
        campos=_campos(
            Campo("numero_controle_pncp", "id"),
            Campo("numero_controle_pncp_compra", "id"),
            Campo("orgao_cnpj", "id", agrupavel=True),
            Campo("orgao_razao_social", "categoria", agrupavel=True),
            Campo("esfera_id", "categoria", agrupavel=True),
            Campo("uf_sigla", "categoria", agrupavel=True),
            Campo("municipio_nome", "categoria", agrupavel=True),
            Campo("ano_contrato", "inteiro", agrupavel=True),
            Campo("tipo_contrato_nome", "categoria", agrupavel=True),
            Campo("categoria_processo_nome", "categoria", agrupavel=True),
            Campo("objeto_contrato", "texto"),
            Campo("tipo_pessoa", "categoria", agrupavel=True),
            Campo("ni_fornecedor", "id", agrupavel=True),
            Campo("nome_fornecedor", "categoria", agrupavel=True),
            Campo("valor_inicial", "valor", agregavel=True),
            Campo("valor_global", "valor", agregavel=True),
            Campo("valor_acumulado", "valor", agregavel=True),
            Campo("valor_parcela", "valor", agregavel=True),
            Campo("numero_parcelas", "inteiro", agregavel=True),
            Campo("data_assinatura", "data"),
            Campo("data_vigencia_fim", "data"),
            Campo("mes_assinatura", "texto", filtravel=False, agrupavel=True, expr="substr(data_assinatura, 1, 7)"),
        ),
    ),
}

SUFIXOS = {"__gte": ">=", "__lte": "<=", "__contem": "contem"}
_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def campos_disponiveis(entidade: str) -> list[str]:
    """Os nomes que o analisador pode usar, para entrar no prompt dele."""
    return sorted(ENTIDADES[entidade].campos)


# --------------------------------------------------------------------------- validação do plano


class PlanoInvalido(ValueError):
    def __init__(self, problemas: list[str]):
        self.problemas = problemas
        super().__init__("; ".join(problemas))


def _separar_filtro(chave: str) -> tuple[str, str]:
    for sufixo in SUFIXOS:
        if chave.endswith(sufixo):
            return chave[: -len(sufixo)], sufixo
    return chave, ""


def validar_plano(plano: PlanoConsulta) -> None:
    """Confere o plano contra a tabela de capacidades. Levanta `PlanoInvalido` com todos os problemas."""
    problemas: list[str] = []

    if plano.rota in (Rota.ESTRUTURADA, Rota.HIBRIDA) and Natureza.ESTRUTURADA not in plano.naturezas and plano.rota is Rota.ESTRUTURADA:
        problemas.append("a rota estruturada exige a natureza estruturada")
    if plano.rota is Rota.AGREGADA:
        problemas.append("a base agregada (PCA) ainda não está disponível neste cenário")
    if plano.rota is Rota.CATALOGO:
        return
    if plano.entidade is None:
        if plano.rota is Rota.HIBRIDA and Natureza.ESTRUTURADA in plano.naturezas:
            problemas.append("a rota híbrida com natureza estruturada exige a entidade")
        if problemas:
            raise PlanoInvalido(problemas)
        return

    entidade = ENTIDADES.get(plano.entidade)
    if entidade is None:
        raise PlanoInvalido(problemas + [f"entidade desconhecida: {plano.entidade!r} (use {sorted(ENTIDADES)})"])

    for chave, valor in plano.filtros.items():
        nome, sufixo = _separar_filtro(chave)
        campo = entidade.campos.get(nome)
        if campo is None:
            problemas.append(f"filtro em campo desconhecido: {nome!r}")
            continue
        if not campo.filtravel:
            problemas.append(f"o campo {nome!r} não aceita filtro")
            continue
        if valor is None:
            problemas.append(f"filtro {chave!r} sem valor")
        elif sufixo == "__contem" and campo.tipo not in ("texto", "categoria"):
            problemas.append(f"__contem só vale para campos de texto, e {nome!r} é {campo.tipo}")
        elif sufixo in ("__gte", "__lte"):
            if campo.tipo not in ("inteiro", "valor", "data"):
                problemas.append(f"{sufixo} só vale para número ou data, e {nome!r} é {campo.tipo}")
            elif campo.tipo == "data" and not (isinstance(valor, str) and _DATA.match(valor)):
                problemas.append(f"data inválida em {chave!r}: use AAAA-MM-DD")
            elif campo.tipo != "data" and (isinstance(valor, bool) or not isinstance(valor, (int, float))):
                problemas.append(f"{chave!r} exige um número")
        elif campo.tipo == "data" and not (isinstance(valor, str) and _DATA.match(valor)):
            problemas.append(f"data inválida em {chave!r}: use AAAA-MM-DD")
        elif campo.tipo in ("inteiro", "valor") and (isinstance(valor, bool) or not isinstance(valor, (int, float))):
            problemas.append(f"{chave!r} exige um número")

    if plano.campo_agregacao:
        campo = entidade.campos.get(plano.campo_agregacao)
        if campo is None:
            problemas.append(f"campo de agregação desconhecido: {plano.campo_agregacao!r}")
        elif not campo.agregavel and set(plano.operacoes) & {"somar", "media", "minimo", "maximo"}:
            problemas.append(f"não dá para somar nem tirar média de {plano.campo_agregacao!r} ({campo.tipo})")
        elif not campo.agregavel and not plano.operacoes:
            problemas.append(f"o campo {plano.campo_agregacao!r} não serve para ordenar")

    for nome in plano.agrupar_por:
        campo = entidade.campos.get(nome)
        if campo is None:
            problemas.append(f"campo de agrupamento desconhecido: {nome!r}")
        elif not campo.agrupavel:
            problemas.append(f"não dá para agrupar por {nome!r}")

    if problemas:
        raise PlanoInvalido(problemas)


# --------------------------------------------------------------------------- consulta controlada


@dataclass
class ResultadoSQL:
    entidade: str
    colunas: list[str]
    linhas: list[dict[str, Any]]
    descricao: str
    sql: str
    parametros: list[Any]
    limite: int
    truncado: bool = False
    avisos: list[str] = field(default_factory=list)


def localizar_banco(pastas: Pastas, versao: str | None = None) -> Path:
    """O banco normalizado da versão pedida, ou o da mais recente que tenha um."""
    pasta = pastas.dados / "normalized" / pastas.cenario_id
    if versao:
        candidatos = [versao]
    else:
        candidatos = list(reversed(versoes_existentes(pastas)))
    for v in candidatos:
        caminho = pasta / f"{v}.sqlite"
        if caminho.is_file():
            return caminho
    raise FileNotFoundError(f"nenhum banco normalizado em {pasta}. Rode `python -m src.normalize`.")


def _abrir_somente_leitura(banco: Path) -> sqlite3.Connection:
    conexao = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA query_only = ON")
    # Compara texto sem acento e sem diferença de maiúsculas ("Dispensa" casa com "dispensa").
    conexao.create_function("sem_acento", 1, lambda v: _sem_acento(v) if isinstance(v, str) else v, deterministic=True)
    return conexao


def _escapar_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _montar_filtros(entidade: Entidade, filtros: dict[str, Any]) -> tuple[list[str], list[Any], list[str]]:
    clausulas: list[str] = []
    parametros: list[Any] = []
    partes: list[str] = []
    for chave in sorted(filtros):
        valor = filtros[chave]
        nome, sufixo = _separar_filtro(chave)
        campo = entidade.campos[nome]
        if sufixo == "__contem":
            clausulas.append(f"sem_acento({campo.sql}) LIKE ? ESCAPE '\\'")
            parametros.append(f"%{_escapar_like(_sem_acento(str(valor)))}%")
            partes.append(f"{nome} contém «{valor}»")
        elif sufixo:
            clausulas.append(f"{campo.sql} {SUFIXOS[sufixo]} ?")
            parametros.append(valor)
            partes.append(f"{nome} {'>=' if sufixo == '__gte' else '<='} {valor}")
        elif campo.tipo == "categoria":
            clausulas.append(f"sem_acento({campo.sql}) = ?")
            parametros.append(_sem_acento(str(valor)))
            partes.append(f"{nome} = {valor}")
        else:
            clausulas.append(f"{campo.sql} = ?")
            parametros.append(valor)
            partes.append(f"{nome} = {valor}")
    return clausulas, parametros, partes


_FUNCOES = {"somar": "SUM", "media": "AVG", "minimo": "MIN", "maximo": "MAX"}
_ROTULOS = {"somar": "soma", "media": "media", "minimo": "minimo", "maximo": "maximo"}


def consultar(plano: PlanoConsulta, banco: Path) -> ResultadoSQL:
    """Executa a parte estruturada do plano. Valida antes; só lê."""
    validar_plano(plano)
    if plano.entidade is None:
        raise PlanoInvalido(["o plano não indica a entidade a consultar"])
    entidade = ENTIDADES[plano.entidade]
    limite = plano.limite or LIMITE_PADRAO
    clausulas, parametros, partes = _montar_filtros(entidade, plano.filtros)
    onde = f" WHERE {' AND '.join(clausulas)}" if clausulas else ""
    operacoes = [o for o in plano.operacoes if o != "listar"]
    campo_agr = entidade.campos[plano.campo_agregacao] if plano.campo_agregacao else None
    avisos: list[str] = []

    if operacoes or plano.agrupar_por:
        grupos = [entidade.campos[g] for g in plano.agrupar_por]
        selecao = [f"{g.sql} AS {g.nome}" for g in grupos]
        metricas = ["COUNT(*) AS total"]
        if campo_agr is not None:
            for op in operacoes:
                if op in _FUNCOES:
                    metricas.append(f"{_FUNCOES[op]}({campo_agr.sql}) AS {_ROTULOS[op]}_{campo_agr.nome}")
            metricas.append(f"COUNT({campo_agr.sql}) AS com_valor_{campo_agr.nome}")
        selecao += metricas
        sql = f"SELECT {', '.join(selecao)} FROM {entidade.tabela}{onde}"
        if grupos:
            sql += " GROUP BY " + ", ".join(g.sql for g in grupos)
            ordem = [m for m in metricas if m.startswith(("SUM", "AVG", "MAX", "MIN"))]
            principal = ordem[0].split(" AS ")[1] if ordem else "total"
            sql += f" ORDER BY {principal} DESC, {grupos[0].sql}"
        sql += f" LIMIT {int(limite) + 1}"
        verbo = ", ".join(operacoes) or "agrupar"
        descricao = f"{verbo} em {entidade.tabela}" + (f" de {campo_agr.nome}" if campo_agr else "")
        if grupos:
            descricao += " agrupado por " + ", ".join(g.nome for g in grupos)
    else:
        colunas = list(entidade.exibir)
        ordem = campo_agr.sql if campo_agr else entidade.ordem_padrao
        if campo_agr and campo_agr.nome not in colunas:
            colunas.append(campo_agr.nome)
        sql = (f"SELECT {', '.join(colunas)} FROM {entidade.tabela}{onde} "
               f"ORDER BY {ordem} DESC, numero_controle_pncp LIMIT {int(limite) + 1}")
        descricao = f"listar {entidade.tabela}" + (f" ordenado por {campo_agr.nome} (maiores primeiro)" if campo_agr else " (mais recentes primeiro)")
    if partes:
        descricao += " com " + " e ".join(partes)

    conexao = _abrir_somente_leitura(banco)
    inicio = time.monotonic()

    def _estourou() -> int:
        return 1 if time.monotonic() - inicio > TEMPO_MAXIMO_CONSULTA_S else 0

    conexao.set_progress_handler(_estourou, 10_000)
    try:
        try:
            linhas = [dict(r) for r in conexao.execute(sql, parametros)]
        except sqlite3.OperationalError as erro:
            if "interrupted" in str(erro):
                raise TimeoutError(f"a consulta passou de {TEMPO_MAXIMO_CONSULTA_S:.0f} s e foi cancelada") from erro
            raise
    finally:
        conexao.close()

    truncado = len(linhas) > limite
    linhas = linhas[:limite]
    if truncado:
        avisos.append(f"Mostrando só os {limite} primeiros resultados; há mais.")
    return ResultadoSQL(
        entidade=plano.entidade,
        colunas=list(linhas[0]) if linhas else [],
        linhas=linhas,
        descricao=descricao,
        sql=sql,
        parametros=parametros,
        limite=limite,
        truncado=truncado,
        avisos=avisos,
    )
