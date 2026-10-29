"""Pipeline completo: pergunta -> guardrail -> analisador -> rota -> evidências -> resposta com fontes.

A LLM faz duas coisas, e só duas: transforma a pergunta em um plano (JSON validado) e escreve a resposta
a partir das evidências numeradas. Quem decide a rota, monta o SQL e confere as citações é código comum.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from config import Settings
from src.capacidades import Capacidades, capacidades_do_cenario
from src.fetch_corpus import Pastas, pastas_do_cenario
from src.index import EmbeddingIndisponivel, IndiceCarregado, carregar_indice, criar_embedder
from src.llm import LLM, LLMIndisponivel, criar_llm
from src.obs import PipelineLogger, Stage, Tokens
from src.pii import mascarar_dados_pessoais
from src.retrieve import buscar_por_bases
from src.router import _sem_acento, PlanoInvalido, ResultadoSQL, avaliar_pergunta, consultar, localizar_banco, validar_plano
from src.schema import Fonte, Natureza, PlanoConsulta, RAGResponse, Rota

MAX_LINHAS_NA_EVIDENCIA = 25
MAX_CARACTERES_DO_TRECHO = 1500
MAX_CARACTERES_DO_VALOR = 200
TENTATIVAS_DO_ANALISADOR = 2
_CITACAO = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


class PerguntaNaoEntendida(ValueError):
    pass


@dataclass
class Contexto:
    """Tudo de que o pipeline precisa; nos testes, cada peça é trocada por uma falsa."""

    llm: LLM
    logger: PipelineLogger
    cap: Capacidades
    banco: Path | None = None
    indices: dict[str, IndiceCarregado] = field(default_factory=dict)
    embedder: Any = None
    hoje: str = field(default_factory=lambda: date.today().isoformat())
    cobertura: str | None = None
    bases: list[str] = field(default_factory=list)
    tokens: Tokens = field(default_factory=Tokens)
    # entidade -> campo -> valores que existem no banco, para os campos com `listar_valores`
    valores_conhecidos: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    @property
    def naturezas(self) -> list[Natureza]:
        n = []
        if self.banco is not None:
            n.append(Natureza.ESTRUTURADA)
        if self.indices:
            n.append(Natureza.NORMATIVA)
        return n


@dataclass
class Evidencia:
    numero: int
    chunk_id: str
    base_id: str
    rotulo: str
    texto: str


# --------------------------------------------------------------------------- analisador


def montar_prompt_analise(ctx: Contexto) -> str:
    linhas = [f"Você transforma uma pergunta sobre {ctx.cap.descricao} em um plano de consulta em JSON.", ""]
    rotas = []
    if Natureza.ESTRUTURADA in ctx.naturezas:
        rotas.append("- \"estruturada\": números, contagens, somas, listas e rankings de contratações e contratos (tabelas abaixo).")
    if Natureza.NORMATIVA in ctx.naturezas:
        rotas.append("- \"documentos\": o que dizem os documentos de texto (normas, editais). Exige \"consulta_semantica\".")
    if len(ctx.naturezas) == 2:
        rotas.append("- \"hibrida\": a pergunta mistura dados e norma. Use naturezas [\"estruturada\", \"normativa\"], a entidade, os filtros e a consulta_semantica.")
    rotas.append("- \"catalogo\": SOMENTE quando a pessoa pergunta sobre o próprio sistema (o que ele sabe, que bases e que período existem).")
    rotas.append("- \"fora_do_escopo\": a pergunta é sobre outro assunto, ou pede um dado que as tabelas não têm (por exemplo, um preço por quilo de um item).")
    linhas += ["Rotas disponíveis:", *rotas, "Se a pergunta tem duas partes, uma sobre o que a norma diz (direitos, regras, prazos) e outra sobre dados (quantas, quais, quanto), use \"hibrida\".", ""]
    if Natureza.ESTRUTURADA in ctx.naturezas:
        linhas.append("Entidades e campos (tipo; f=aceita filtro, s=aceita soma/média/mín/máx, g=aceita agrupar):")
        for nome, ent in ctx.cap.entidades.items():
            campos = ", ".join(
                f"{c.nome} ({c.tipo};{'f' if c.filtravel else ''}{'s' if c.agregavel else ''}{'g' if c.agrupavel else ''})"
                + (f" valores: {c.valores_validos()}" if c.valores else "")
                + (f" valores existentes: {' | '.join(ctx.valores_conhecidos[nome][c.nome])}" if c.nome in ctx.valores_conhecidos.get(nome, {}) else "")
                for c in ent.campos.values()
            )
            linhas.append(f"- {nome}: {campos}")
        linhas += [
            "",
            "Filtros: {\"campo\": valor} para igualdade; \"campo__gte\" e \"campo__lte\" para faixas de número ou data (AAAA-MM-DD);",
            "\"campo__contem\" para texto parcial (campos texto ou categoria). Valores de categoria não precisam de acento exato.",
            "Operações: listar, contar, somar, media, minimo, maximo, agrupar. somar/media/minimo/maximo exigem campo_agregacao",
            "(campo com s). agrupar exige agrupar_por (campos com g) e pode vir junto com as outras. Para \"os maiores\", use listar com campo_agregacao.",
            *ctx.cap.dicas_do_analisador,
        ]
    linhas += [
        "",
        "Formato (somente JSON, sem comentários): {\"rota\": ..., \"naturezas\": [\"estruturada\"|\"normativa\"], \"entidade\": ou null,",
        "\"filtros\": {}, \"operacoes\": [], \"campo_agregacao\": ou null, \"agrupar_por\": [], \"limite\": ou null, \"consulta_semantica\": ou null}",
        "Nunca escreva SQL. Use só os campos listados. Se a pergunta não puder ser respondida com essas rotas, use \"catalogo\".",
    ]
    return "\n".join(linhas)


def _extrair_json(texto: str) -> dict[str, Any]:
    ini, fim = texto.find("{"), texto.rfind("}")
    if ini < 0 or fim <= ini:
        raise ValueError("a resposta não contém um objeto JSON")
    dados = json.loads(texto[ini : fim + 1])
    if not isinstance(dados, dict):
        raise ValueError("o JSON não é um objeto")
    return dados


def analisar(pergunta: str, ctx: Contexto) -> PlanoConsulta:
    """Pede o plano à LLM; se vier inválido, devolve a ela a lista de problemas uma vez."""
    sistema = montar_prompt_analise(ctx)
    usuario = pergunta
    ultimo = ""
    for tentativa in range(1, TENTATIVAS_DO_ANALISADOR + 1):
        with ctx.logger.etapa(Stage.QUERY_ANALYSIS, pergunta) as e:
            c = ctx.llm.completar(sistema, usuario, json=True)
            e.modelo, e.output = c.modelo, c.texto
            e.tokens.somar(c.tokens)
            ctx.tokens.somar(c.tokens)
            e.extra = {"tentativa": tentativa}
        try:
            plano = PlanoConsulta.model_validate(_extrair_json(c.texto))
            validar_plano(plano, ctx.cap)
            _conferir_disponibilidade(plano, ctx)
            _conferir_valores(plano, ctx)
            return plano
        except (ValueError, ValidationError, PlanoInvalido) as erro:
            ultimo = "; ".join(erro.problemas) if isinstance(erro, PlanoInvalido) else str(erro).replace("\n", " ")
            usuario = f"{pergunta}\n\nSua resposta anterior foi recusada pelo validador: {ultimo}\nCorrija e responda só com o JSON."
    raise PerguntaNaoEntendida(ultimo)


def _conferir_valores(plano: PlanoConsulta, ctx: Contexto) -> None:
    """Filtro de igualdade em campo com lista de valores existentes: o valor tem de estar na lista."""
    conhecidos = ctx.valores_conhecidos.get(plano.entidade or "", {})
    problemas = []
    for chave, valor in plano.filtros.items():
        if chave in conhecidos and not any(_sem_acento(str(valor)) == _sem_acento(v) for v in conhecidos[chave]):
            problemas.append(f"o valor {valor!r} não existe em {chave}; use um destes: {' | '.join(conhecidos[chave])}")
    if problemas:
        raise PlanoInvalido(problemas)


def _conferir_disponibilidade(plano: PlanoConsulta, ctx: Contexto) -> None:
    faltam = [n for n in plano.naturezas if n in (Natureza.ESTRUTURADA, Natureza.NORMATIVA) and n not in ctx.naturezas]
    if faltam:
        raise PlanoInvalido([f"a base {n.value} não está carregada neste ambiente" for n in faltam])


# --------------------------------------------------------------------------- evidências


def _valor(v: Any) -> str:
    texto = "vazio" if v is None else str(v)
    return texto if len(texto) <= MAX_CARACTERES_DO_VALOR else texto[:MAX_CARACTERES_DO_VALOR] + "…"


def evidencia_sql(r: ResultadoSQL, numero: int) -> Evidencia:
    linhas = [f"Consulta: {r.descricao}.", f"{len(r.linhas)} linha(s) retornada(s)."]
    for avis in r.avisos:
        linhas.append(f"ATENÇÃO: {avis}")
    for l in r.linhas[:MAX_LINHAS_NA_EVIDENCIA]:
        linhas.append("; ".join(f"{k}={_valor(v)}" for k, v in l.items()))
    chave = hashlib.sha256((r.sql + json.dumps(r.parametros, default=str)).encode()).hexdigest()[:10]
    return Evidencia(numero, f"sql:{r.entidade}:{chave}", "base_estruturada", f"Consulta à base estruturada ({r.entidade}): {r.descricao}", "\n".join(linhas))


def evidencias_documentos(resultados: list, primeiro: int) -> list[Evidencia]:
    saida = []
    for i, r in enumerate(resultados):
        m = r.metadata
        rotulo = " — ".join(x for x in (m.get("norma"), m.get("rotulo")) if x) or m.get("source_file", r.chunk_id)
        saida.append(Evidencia(primeiro + i, r.chunk_id, m.get("base_id", "?"), rotulo, r.texto[:MAX_CARACTERES_DO_TRECHO]))
    return saida


def coletar(plano: PlanoConsulta, pergunta: str, ctx: Contexto) -> tuple[list[Evidencia], list[str]]:
    """Executa as partes do plano. Devolve as evidências e avisos sobre o que não deu para consultar."""
    evidencias: list[Evidencia] = []
    avisos: list[str] = []
    usa_sql = plano.rota in (Rota.ESTRUTURADA, Rota.HIBRIDA) and plano.entidade and ctx.banco
    usa_docs = plano.rota in (Rota.DOCUMENTOS, Rota.HIBRIDA) and ctx.indices

    if usa_sql:
        with ctx.logger.etapa(Stage.ROTEAMENTO, plano.model_dump_json()) as e:
            try:
                r = consultar(plano, ctx.banco, ctx.cap)
            except sqlite3.Error as erro:
                raise RuntimeError(f"A base estruturada não pôde ser consultada: {erro}") from erro
            e.output = f"{len(r.linhas)} linhas"
            e.extra = {"sql": r.sql, "parametros": [str(p) for p in r.parametros], "truncado": r.truncado}
        evidencias.append(evidencia_sql(r, len(evidencias) + 1))
    if usa_docs:
        consulta = plano.consulta_semantica or pergunta
        achados = buscar_por_bases(
            ctx.indices, ctx.embedder, consulta, k=5,
            filtros={"vigente_em": ctx.hoje, "sensitivity": ["publico"]}, logger=ctx.logger,
        )
        evidencias += evidencias_documentos(achados, len(evidencias) + 1)
        if not achados:
            avisos.append("Nenhum trecho de norma foi encontrado para a pergunta.")
    return evidencias, avisos


# --------------------------------------------------------------------------- geração e validação

def sistema_geracao(cap: Capacidades) -> str:
    regras = [
        "Cite a evidência com [n] logo depois de cada afirmação, por exemplo \"Foram 896 dispensas [1].\" Nunca cite um número que não existe.",
        "Use apenas fatos e números das evidências. Não complete com conhecimento próprio. Se a evidência não basta, diga o que falta.",
        *cap.regras_de_resposta,
        "Se a evidência avisar que a lista foi cortada, diga que há mais resultados.",
        "Responda em português, direto, em até cerca de 8 linhas.",
    ]
    return (f"Você responde perguntas sobre {cap.descricao} usando SOMENTE as evidências numeradas que vêm abaixo.\n\nRegras:\n"
            + "\n".join(f"- {r}" for r in regras))


def _prompt_geracao(pergunta: str, evidencias: list[Evidencia], avisos: list[str], correcao: str = "") -> str:
    blocos = [f"[{e.numero}] {e.rotulo}\n{e.texto}" for e in evidencias]
    extra = ("\n\nObservações do sistema: " + " ".join(avisos)) if avisos else ""
    return f"Pergunta: {pergunta}\n\nEvidências:\n\n" + "\n\n".join(blocos) + extra + (f"\n\n{correcao}" if correcao else "")


def _citados(texto: str) -> list[int]:
    return [int(n) for grupo in _CITACAO.findall(texto) for n in re.split(r"\s*,\s*", grupo)]


def _tirar_citacoes_invalidas(texto: str, validos: set[int]) -> str:
    def trocar(m: re.Match[str]) -> str:
        numeros = [int(n) for n in re.split(r"\s*,\s*", m.group(1)) if int(n) in validos]
        return f"[{', '.join(map(str, numeros))}]" if numeros else ""

    return _CITACAO.sub(trocar, texto).replace(" .", ".").strip()


def gerar(pergunta: str, evidencias: list[Evidencia], avisos: list[str], ctx: Contexto) -> tuple[str, list[Evidencia]] | None:
    """Escreve a resposta e confere as citações. Uma tentativa extra se citar errado ou não citar nada."""
    validos = {e.numero for e in evidencias}
    correcao = ""
    texto = ""
    for tentativa in range(1, 3):
        with ctx.logger.etapa(Stage.GERACAO, pergunta) as e:
            c = ctx.llm.completar(sistema_geracao(ctx.cap), _prompt_geracao(pergunta, evidencias, avisos, correcao))
            e.modelo, e.output = c.modelo, c.texto
            e.tokens.somar(c.tokens)
            ctx.tokens.somar(c.tokens)
            e.extra = {"tentativa": tentativa, "evidencias": len(evidencias)}
        texto = c.texto.strip()
        citados = _citados(texto)
        ruins = sorted(set(citados) - validos)
        with ctx.logger.etapa(Stage.VALIDACAO, texto) as v:
            v.output = f"citadas={sorted(set(citados))} invalidas={ruins}"
        if citados and not ruins:
            break
        correcao = (
            f"Sua resposta anterior citou fontes que não existem: {ruins}. " if ruins else "Sua resposta anterior não citou nenhuma evidência. "
        ) + f"Refaça citando apenas {sorted(validos)} com o formato [n]."
    texto = _tirar_citacoes_invalidas(texto, validos)
    usadas = sorted(set(_citados(texto)))
    if not usadas:
        return None
    return mascarar_dados_pessoais(texto), [e for e in evidencias if e.numero in usadas]


# --------------------------------------------------------------------------- pipeline


def _recusa(motivo: str, mensagem: str, ctx: Contexto, rotas: list[Rota] | None = None) -> RAGResponse:
    return RAGResponse(answer=mensagem, is_refusal=True, refusal_reason=motivo, rotas=rotas or [], trace_id=ctx.logger.query_id)


def _resposta_catalogo(ctx: Contexto) -> str:
    cat = ctx.cap.catalogo
    partes = ["Posso consultar:"]
    if Natureza.ESTRUTURADA in ctx.naturezas and cat.get("estruturada"):
        partes.append(f"- {cat['estruturada']};")
    if Natureza.NORMATIVA in ctx.naturezas and cat.get("normativa"):
        partes.append(f"- {cat['normativa']}.")
    if ctx.cobertura:
        partes.append(f"Cobertura: {ctx.cobertura}")
    if cat.get("aviso"):
        partes.append(cat["aviso"])
    return "\n".join(partes)


def responder(pergunta: str, ctx: Contexto) -> RAGResponse:
    with ctx.logger.etapa(Stage.GUARDRAIL, pergunta) as e:
        decisao = avaliar_pergunta(pergunta, ctx.cap)
        e.output = "permitida" if decisao.permitida else f"recusada: {decisao.categoria}"
    if not decisao.permitida:
        return _recusa(decisao.categoria or "recusada", decisao.mensagem or "Não posso responder a essa pergunta.", ctx)

    if not ctx.naturezas:
        return _recusa("sem_dados", "Nenhuma base está carregada neste ambiente. Rode a coleta, a normalização e a indexação do cenário.", ctx)

    try:
        plano = analisar(pergunta, ctx)
    except PerguntaNaoEntendida:
        return _recusa("pergunta_nao_entendida", "Não consegui transformar a pergunta em uma consulta que eu saiba fazer. Tente reformular, citando o órgão, o período ou o tipo de contratação.", ctx)

    if plano.rota is Rota.FORA_DO_ESCOPO:
        return _recusa("fora_do_escopo", "Não consigo responder a isso com as bases que tenho.\n" + _resposta_catalogo(ctx), ctx, [Rota.FORA_DO_ESCOPO])

    if plano.rota is Rota.CATALOGO:
        return RAGResponse(answer=_resposta_catalogo(ctx), rotas=[Rota.CATALOGO], cobertura=ctx.cobertura, trace_id=ctx.logger.query_id)

    evidencias, avisos = coletar(plano, pergunta, ctx)
    if not evidencias:
        return _recusa("sem_evidencia", "Não encontrei nas bases disponíveis informação para responder a essa pergunta.", ctx, [plano.rota])

    gerado = gerar(pergunta, evidencias, avisos, ctx)
    if gerado is None:
        return _recusa("sem_fonte_verificavel", "Não consegui escrever uma resposta com fontes verificáveis. Tente reformular a pergunta.", ctx, [plano.rota])
    texto, usadas = gerado
    return RAGResponse(
        answer=texto,
        sources_used=[Fonte(numero=e.numero, chunk_id=e.chunk_id, base_id=e.base_id, rotulo=e.rotulo) for e in usadas],
        rotas=[plano.rota],
        cobertura=ctx.cobertura,
        trace_id=ctx.logger.query_id,
    )


# --------------------------------------------------------------------------- montagem do ambiente real


def listar_valores_existentes(banco: Path, cap: Capacidades, limite: int = 40) -> dict[str, dict[str, list[str]]]:
    """Valores distintos dos campos marcados com `listar_valores`, se forem poucos (até `limite`)."""
    achados: dict[str, dict[str, list[str]]] = {}
    conexao = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
    try:
        for nome, ent in cap.entidades.items():
            for campo in ent.campos.values():
                if not campo.listar_valores or campo.expr:
                    continue
                try:
                    linhas = conexao.execute(
                        f"SELECT DISTINCT {campo.nome} FROM {ent.tabela} WHERE {campo.nome} IS NOT NULL ORDER BY 1 LIMIT {limite + 1}"
                    ).fetchall()
                except sqlite3.OperationalError:  # tabela ou coluna que este banco não tem
                    continue
                if 0 < len(linhas) <= limite:
                    achados.setdefault(nome, {})[campo.nome] = [str(l[0]) for l in linhas]
    finally:
        conexao.close()
    return achados


def carregar_contexto(config: Settings, pastas: Pastas) -> Contexto:
    logger = PipelineLogger(config.log_dir)
    cap = capacidades_do_cenario(pastas.cenario_id)
    try:
        banco = localizar_banco(pastas)
    except FileNotFoundError:
        banco = None
    cobertura = None
    if banco is not None:
        c = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
        try:
            rel = json.loads(c.execute("SELECT valor FROM metadados WHERE chave = 'relatorio'").fetchone()[0])
        finally:
            c.close()
        p = rel["periodo_de_publicacao"]
        rotulo = cap.catalogo.get("cobertura", "dados publicados")
        cobertura = f"{rotulo} de {p['de']} a {p['ate']} (versão {rel['versao']})"
    indices: dict[str, IndiceCarregado] = {}
    embedder = None
    pasta_indices = pastas.dados / "index" / pastas.cenario_id
    if pasta_indices.is_dir():
        embedder = criar_embedder(config)
        for pasta in sorted(p for p in pasta_indices.iterdir() if (p / "estado_indexacao.json").exists()):
            indices[pasta.name] = carregar_indice(pasta, embedder.identity)
    return Contexto(llm=criar_llm(config), logger=logger, cap=cap, banco=banco, indices=indices, embedder=embedder,
                    cobertura=cobertura, bases=sorted(indices),
                    valores_conhecidos=listar_valores_existentes(banco, cap) if banco is not None else {})


def formatar(resposta: RAGResponse) -> str:
    saida = [resposta.answer]
    if resposta.sources_used:
        saida += ["", "Fontes:"] + [f"  [{f.numero}] {f.rotulo or f.chunk_id}" for f in resposta.sources_used]
    if resposta.cobertura and not resposta.is_refusal:
        saida.append(f"\nCobertura: {resposta.cobertura}")
    return "\n".join(saida)


def main(argv: list[str] | None = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m src.generate", description="Faz uma pergunta ao sistema.")
    p.add_argument("pergunta", nargs="*", help="sem pergunta, abre um modo interativo")
    p.add_argument("--scenario", default=None, choices=("rj", "saude", "pesquisa"))
    p.add_argument("--json", action="store_true", help="imprime a resposta estruturada")
    args = p.parse_args(argv)
    config = Settings() if not args.scenario else Settings(scenario=args.scenario)
    try:
        ctx = carregar_contexto(config, pastas_do_cenario(config.scenario))
    except (LLMIndisponivel, FileNotFoundError, ValueError) as erro:
        print(f"Erro: {erro}", file=sys.stderr)
        return 2

    def perguntar(texto: str) -> int:
        ctx.logger.query_id = f"q_{hashlib.sha256(texto.encode()).hexdigest()[:6]}"
        try:
            r = responder(texto, ctx)
        except (LLMIndisponivel, EmbeddingIndisponivel, RuntimeError) as erro:
            print(f"Erro: {erro}", file=sys.stderr)
            return 3
        print(r.model_dump_json(indent=2) if args.json else formatar(r))
        return 0

    if args.pergunta:
        return perguntar(" ".join(args.pergunta))
    print("Pergunte sobre contratações públicas do RJ (linha vazia para sair).")
    while True:
        try:
            texto = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if not texto:
            return 0
        perguntar(texto)


if __name__ == "__main__":
    raise SystemExit(main())
