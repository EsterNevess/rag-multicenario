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

from src.capacidades import Capacidades, Entidade, capacidades_do_cenario
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


# Regras de qualquer cenário (categoria, mensagem, padrões sobre o texto sem acento e em minúsculas).
# As regras próprias de cada cenário ficam em `capacidades.yaml`.
REGRAS_COMUNS: list[tuple[str, str, list[str]]] = [
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
]
_REGRAS_COMUNS_COMPILADAS = [(c, m, [re.compile(p) for p in ps]) for c, m, ps in REGRAS_COMUNS]


def _capacidades(cap: Capacidades | None) -> Capacidades:
    """Sem cenário explícito vale o do RJ, o primeiro. O pipeline sempre passa o do cenário ativo."""
    return cap or capacidades_do_cenario("rj")


def _regras(cap: Capacidades) -> list[tuple[str, str, list[re.Pattern[str]]]]:
    proprias = [(r.categoria, r.mensagem, [re.compile(p) for p in r.padroes]) for r in cap.regras_de_entrada]
    return _REGRAS_COMUNS_COMPILADAS + proprias


def avaliar_pergunta(pergunta: str, cap: Capacidades | None = None) -> Decisao:
    """Primeira barreira: decide se a pergunta pode seguir para o analisador."""
    if not pergunta or not pergunta.strip():
        return Decisao(False, "pergunta_vazia", "Escreva uma pergunta sobre contratações públicas do RJ.")
    if len(pergunta) > TAMANHO_MAXIMO_PERGUNTA:
        return Decisao(False, "pergunta_longa", f"A pergunta passa de {TAMANHO_MAXIMO_PERGUNTA} caracteres. Resuma em uma ou duas frases.")
    texto = _sem_acento(unicodedata.normalize("NFKC", pergunta))
    for categoria, mensagem, padroes in _regras(_capacidades(cap)):
        if any(p.search(texto) for p in padroes):
            return Decisao(False, categoria, mensagem)
    return Decisao(True)


def avaliar_resposta(texto: str, cap: Capacidades | None = None) -> Decisao:
    """Última barreira: a resposta pronta não pode casar com uma regra de saída do cenário (por exemplo, revelar um revisor)."""
    comparavel = _sem_acento(unicodedata.normalize("NFKC", texto))
    for regra in _capacidades(cap).regras_de_saida:
        if any(re.search(p, comparavel) for p in regra.padroes):
            return Decisao(False, regra.categoria, regra.mensagem)
    return Decisao(True)


# --------------------------------------------------------------------------- tabela de capacidades


SUFIXOS = {"__gte": ">=", "__lte": "<=", "__contem": "contem"}
_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


def usa_base_agregada(plano: PlanoConsulta, cap: Capacidades) -> bool:
    """A parte de dados do plano é um indicador da base agregada (e não uma tabela de dados)?"""
    return plano.rota is Rota.AGREGADA or (plano.rota is Rota.HIBRIDA and plano.entidade in cap.entidades_agregadas())


def _mapa_de_entidades(plano: PlanoConsulta, cap: Capacidades) -> dict[str, Entidade]:
    return cap.entidades_agregadas() if usa_base_agregada(plano, cap) else cap.entidades


def _problemas_dos_filtros_de_documentos(plano: PlanoConsulta, cap: Capacidades) -> list[str]:
    """`vigente_em` é AAAA-MM-DD ou "todas"; os demais filtros têm de ser campos que o cenário declara, com texto."""
    problemas = []
    for chave, valor in plano.filtros_documentos.items():
        if chave == "vigente_em":
            if not (valor == "todas" or (isinstance(valor, str) and _DATA.match(valor))):
                problemas.append(f"vigente_em exige uma data AAAA-MM-DD ou \"todas\", e veio {valor!r}")
        elif chave not in cap.filtros_de_documentos:
            validos = ["vigente_em", *cap.filtros_de_documentos]
            problemas.append(f"filtro de documentos desconhecido: {chave!r} (use {validos})")
        elif not isinstance(valor, str) or not valor.strip():
            problemas.append(f"o filtro de documentos {chave!r} exige um texto")
    return problemas


def validar_plano(plano: PlanoConsulta, cap: Capacidades | None = None) -> None:
    """Confere o plano contra a tabela de capacidades. Levanta `PlanoInvalido` com todos os problemas."""
    problemas: list[str] = []

    if plano.rota in (Rota.ESTRUTURADA, Rota.HIBRIDA) and Natureza.ESTRUTURADA not in plano.naturezas and plano.rota is Rota.ESTRUTURADA:
        problemas.append("a rota estruturada exige a natureza estruturada")
    cap = _capacidades(cap)
    if plano.rota in (Rota.DOCUMENTOS, Rota.HIBRIDA):
        problemas += _problemas_dos_filtros_de_documentos(plano, cap)
    if plano.rota is Rota.AGREGADA:
        if cap.catalogo_de_indicadores is None:
            problemas.append("este cenário não tem base agregada")
        elif Natureza.AGREGADA not in plano.naturezas:
            problemas.append("a rota agregada exige a natureza agregada")
    if plano.rota in (Rota.CATALOGO, Rota.FORA_DO_ESCOPO):
        return
    if plano.entidade is None:
        if plano.rota is Rota.HIBRIDA and Natureza.ESTRUTURADA in plano.naturezas:
            problemas.append("a rota híbrida com natureza estruturada exige a entidade")
        if problemas:
            raise PlanoInvalido(problemas)
        return

    entidades = _mapa_de_entidades(plano, cap)
    entidade = entidades.get(plano.entidade)
    if entidade is None:
        raise PlanoInvalido(problemas + [f"entidade desconhecida: {plano.entidade!r} (use {sorted(entidades)})"])

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
        elif campo.valores and sufixo:
            problemas.append(f"o campo {nome!r} é codificado ({campo.valores_validos()}); use só igualdade")
        elif campo.valores and campo.traduzir(valor) is None:
            problemas.append(f"valor {valor!r} inválido para {nome!r}; use um destes: {campo.valores_validos()}")
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
        elif campo.valores:
            codigo = campo.traduzir(valor)
            clausulas.append(f"{campo.sql} = ?")
            parametros.append(codigo)
            partes.append(f"{nome} = {codigo} ({campo.valores[codigo]})")
        elif campo.tipo == "categoria":
            oficial = campo.canonico(valor)
            clausulas.append(f"sem_acento({campo.sql}) = ?")
            parametros.append(_sem_acento(str(oficial)))
            partes.append(f"{nome} = {oficial}" + (f" (apelido {valor})" if oficial != valor else ""))
        else:
            clausulas.append(f"{campo.sql} = ?")
            parametros.append(valor)
            partes.append(f"{nome} = {valor}")
    return clausulas, parametros, partes


_FUNCOES = {"somar": "SUM", "media": "AVG", "minimo": "MIN", "maximo": "MAX"}
_ROTULOS = {"somar": "soma", "media": "media", "minimo": "minimo", "maximo": "maximo"}


def consultar(plano: PlanoConsulta, banco: Path, cap: Capacidades | None = None) -> ResultadoSQL:
    """Executa a parte estruturada do plano. Valida antes; só lê."""
    cap = _capacidades(cap)
    validar_plano(plano, cap)
    if plano.entidade is None:
        raise PlanoInvalido(["o plano não indica a entidade a consultar"])
    entidade = _mapa_de_entidades(plano, cap)[plano.entidade]
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
               f"ORDER BY {ordem} DESC, {', '.join(entidade.exibir)} LIMIT {int(limite) + 1}")
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
    vazio = not linhas or (len(linhas) == 1 and linhas[0].get("total") == 0 and not plano.agrupar_por)  # contar sem grupos devolve uma linha com 0
    if vazio:
        igualdades = [n for n in plano.filtros if not _separar_filtro(n)[1] and entidade.campos[_separar_filtro(n)[0]].tipo == "categoria"]
        if igualdades:
            avisos.append(f"Nenhum registro casou com os filtros. Filtros de igualdade em campos de nome ({', '.join(igualdades)}) exigem o nome exato; "
                          "uma sigla ou um nome incompleto pode não casar. Isso NÃO prova que não existem registros.")
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
