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
from src.indicadores import conferir_atualizacao, ler_ficha, localizar_agregado
from src.llm import LLM, LLMIndisponivel, PedidoGrandeDemais, PedidoRecusado, criar_llm
from src.numeros import numeros_nao_conferidos
from src.obs import PipelineLogger, Stage, Tokens
from src.pii import mascarar_dados_pessoais
from src.rerank import RerankerIndisponivel, carregar_reranker
from src.retrieve import buscar_por_bases, buscar_por_versao
from src.router import PlanoInvalido, ResultadoSQL, _sem_acento, avaliar_pergunta, avaliar_resposta, consultar, localizar_banco, usa_base_agregada, validar_plano
from src.schema import ChavesDoResultado, Fonte, Natureza, PlanoConsulta, RAGResponse, Rota

MAX_LINHAS_NA_EVIDENCIA = 25
MAX_CARACTERES_DO_TRECHO = 1500
MAX_CARACTERES_DO_VALOR = 200
AVISO_EVIDENCIA_CORTADA = "Parte da evidência foi omitida porque o pedido passava do limite de tamanho do modelo: diga que a resposta pode estar incompleta."
TENTATIVAS_DO_ANALISADOR = 3  # o plano pode errar duas vezes em sequência (por exemplo, a rota e depois um filtro) e ainda ser corrigido
# Palavras que mostram que a pessoa quer um número calculado (e não uma lista de registros).
INTENCAO_AGREGADA = ("proporcao", "percentual", "porcentagem", "quantas", "quantos", "media", "medio", "total", "soma", "evolu", "por mes", "cada mes",
                     "por ano", "mensal", "quanto", "qual a parte", "que parte")
_CITACAO = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
# O modelo às vezes escreve a citação com colchetes largos, 【1】 ou 【1†L10-L12】, e a conferência não a reconheceria (resposta certa recusada).
_CITACAO_LARGA = re.compile("[【［]\\s*(\\d+(?:\\s*,\\s*\\d+)*)\\s*(?:[†‡][^】］]*)?[】］]")


def normalizar_citacoes(texto: str) -> str:
    """Troca 【n】, 【n†linhas】 e ［n］ por [n], o formato que o resto do pipeline confere."""
    return _CITACAO_LARGA.sub(lambda m: f"[{m.group(1)}]", texto)


class PerguntaNaoEntendida(ValueError):
    pass


@dataclass
class Contexto:
    """Tudo de que o pipeline precisa; nos testes, cada peça é trocada por uma falsa."""

    llm: LLM
    logger: PipelineLogger
    cap: Capacidades
    banco: Path | None = None
    banco_agregado: Path | None = None
    indices: dict[str, IndiceCarregado] = field(default_factory=dict)
    embedder: Any = None
    reranker: Any = None  # cross-encoder opcional (Settings.rerank); sem ele, a busca é só BM25 + vetores + RRF
    rerank_candidatos: int = 10  # quantos trechos da fusão o cross-encoder reordena
    hoje: str = field(default_factory=lambda: date.today().isoformat())
    cobertura: str | None = None
    bases: list[str] = field(default_factory=list)
    tokens: Tokens = field(default_factory=Tokens)
    # entidade -> campo -> valores que existem no banco, para os campos com `listar_valores`
    valores_conhecidos: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    natureza_das_bases: dict[str, Natureza] = field(default_factory=dict)  # base_id -> natureza; sem informação, normativa

    def natureza_da_base(self, base_id: str) -> Natureza:
        return self.natureza_das_bases.get(base_id, Natureza.NORMATIVA)

    @property
    def naturezas(self) -> list[Natureza]:
        n = []
        if self.banco is not None:
            n.append(Natureza.ESTRUTURADA)
        if self.banco_agregado is not None and self.cap.catalogo_de_indicadores is not None:
            n.append(Natureza.AGREGADA)
        presentes = {self.natureza_da_base(b) for b in self.indices}
        for natureza in (Natureza.NORMATIVA, Natureza.CONVERSACIONAL):
            if natureza in presentes:
                n.append(natureza)
        return n


@dataclass
class Evidencia:
    numero: int
    chunk_id: str
    base_id: str
    rotulo: str
    texto: str
    chaves: ChavesDoResultado | None = None  # as chaves das linhas listadas, quando a evidência é uma listagem com chave


# --------------------------------------------------------------------------- analisador


def montar_prompt_analise(ctx: Contexto) -> str:
    linhas = [f"Você transforma uma pergunta sobre {ctx.cap.descricao} em um plano de consulta em JSON.", f"Data de hoje: {ctx.hoje} (AAAA-MM-DD).", ""]
    rotas = []
    if Natureza.ESTRUTURADA in ctx.naturezas:
        rotas.append("- \"estruturada\": números, contagens, somas, listas e rankings de contratações e contratos (tabelas abaixo).")
    if Natureza.AGREGADA in ctx.naturezas:
        rotas.append("- \"agregada\": um indicador já calculado (proporção, média, total por ano ou por recorte). Use naturezas [\"agregada\"], "
                     "entidade = o id do indicador, filtros nas dimensões dele e operações []. Quando existir um indicador para o que se pede (proporção, média, total por ano, por mês ou por recorte), "
                     "use SEMPRE esta rota e não a \"estruturada\", mesmo que a pergunta cite também a definição legal: o indicador traz a definição, o recorte e as ressalvas.")
    textuais = []
    if Natureza.NORMATIVA in ctx.naturezas:
        textuais.append("naturezas [\"normativa\"] para o que dizem normas e editais")
    if Natureza.CONVERSACIONAL in ctx.naturezas:
        textuais.append("naturezas [\"conversacional\"] para o que dizem e-mails, atas e pareceres (documentos de comunicação, sintéticos)")
    if textuais:
        rotas.append("- \"documentos\": o que dizem os documentos de texto. Use " + "; ".join(textuais) + ". Exige \"consulta_semantica\".")
    if len(ctx.naturezas) >= 2:
        nomes = " e ".join(f"\"{n.value}\"" for n in ctx.naturezas)
        rotas.append(f"- \"hibrida\": a pergunta mistura tipos de evidência. Use naturezas com pelo menos duas de {nomes}, a entidade (se houver dados: o id do indicador, quando existir um, ou a tabela de dados), os filtros e a consulta_semantica.")
    rotas.append("- \"catalogo\": SOMENTE quando a pessoa pergunta sobre o próprio sistema (o que ele sabe, que bases e que período existem).")
    rotas.append("- \"fora_do_escopo\": a pergunta é sobre outro assunto, ou pede um dado que as tabelas não têm (por exemplo, um preço por quilo de um item).")
    if ctx.cap.filtros_de_documentos and (Natureza.NORMATIVA in ctx.naturezas or Natureza.CONVERSACIONAL in ctx.naturezas):
        rotas.append("  Nas rotas \"documentos\" e \"hibrida\", \"filtros_documentos\" restringe os documentos: "
                     + "; ".join(f"\"{n}\" ({d})" for n, d in ctx.cap.filtros_de_documentos.items())
                     + ". \"vigente_em\" (AAAA-MM-DD) escolhe a versão que valia naquela data; sem ele vale só a versão em vigor hoje, e \"todas\" traz todas as versões "
                     "(use para comparar versões ou para perguntar o que mudou).")
    linhas += ["Rotas disponíveis:", *rotas, "Se a pergunta tem duas partes, uma sobre o que a norma diz (direitos, regras, prazos) e outra sobre dados (quantas, quais, quanto), use \"hibrida\".", ""]
    if Natureza.AGREGADA in ctx.naturezas:
        linhas.append("Indicadores calculados (id: nome; dimensões que aceitam filtro; unidade):")
        for id_, ind in ctx.cap.catalogo_de_indicadores.indicadores.items():
            valores = ctx.valores_conhecidos.get(id_, {})
            extra = "".join(f" [valores de {d}: {' | '.join(v)}]" for d, v in valores.items())
            linhas.append(f"- {id_}: {ind.nome}. {ind.descricao} Dimensões: {', '.join(ind.dimensoes)}; unidade: {ind.unidade}.{extra}")
            for pergunta, filtros in ind.exemplos[:1]:
                linhas.append(f"    exemplo: \"{pergunta}\" -> rota agregada, entidade {id_}, filtros {json.dumps(filtros, ensure_ascii=False)}")
        limites = ctx.cap.catalogo_de_indicadores.limites
        if limites:
            linhas.append("O que NÃO se calcula (nesses casos use \"fora_do_escopo\"): " + " ".join(limites))
        linhas.append("")
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
        f"Formato (somente JSON, sem comentários): {{\"rota\": ..., \"naturezas\": [{'|'.join(chr(34) + n.value + chr(34) for n in ctx.naturezas) or chr(34) + chr(34)}], \"entidade\": ou null,",
        "\"filtros\": {}, " + ("\"filtros_documentos\": {}, " if ctx.cap.filtros_de_documentos else "") + "\"operacoes\": [], \"campo_agregacao\": ou null, \"agrupar_por\": [], \"limite\": ou null, \"consulta_semantica\": ou null, \"do_resultado_anterior\": false}",
        "\"do_resultado_anterior\" só vale numa conversa: true quando a pergunta fala dos registros que a resposta anterior listou (\"desses\", \"entre eles\").",
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


@dataclass(frozen=True)
class TurnoAnterior:
    """O turno anterior da conversa, só o que ajuda a resolver "desses" ou "e em 2025?": a pergunta e o plano que ela gerou.

    O texto da resposta NÃO entra: ele traz trechos de documentos, e o que um documento diz não deve poder dar instruções ao analisador.
    A memória é de um turno só.
    """

    pergunta: str
    plano: PlanoConsulta
    # As chaves (do banco) das linhas que o turno anterior listou. Entram como identificadores, nunca como texto de resposta.
    chaves: ChavesDoResultado | None = None


def mensagem_do_analisador(pergunta: str, anterior: TurnoAnterior | None = None) -> str:
    """O que o analisador lê: a pergunta, ou a pergunta com o turno anterior como contexto (a LLM decide se depende dele)."""
    if anterior is None:
        return pergunta
    plano = {k: v for k, v in anterior.plano.model_dump(mode="json").items() if v not in (None, [], {}, False)}
    lista = ""
    if anterior.chaves and anterior.chaves.valores:
        lista = (f"A resposta anterior listou {len(anterior.chaves.valores)} registro(s) de {anterior.chaves.tipo}. Se a pergunta atual se refere a eles "
                 "(\"desses\", \"entre eles\"), marque \"do_resultado_anterior\": true: o sistema limita a consulta a esses registros, "
                 "então nesse caso não repita os filtros do plano anterior nem escreva identificadores.\n")
    return (
        "Conversa anterior. Use-a SÓ se a pergunta atual depender dela (por exemplo \"desses\", \"e em 2025?\", \"e nos municípios?\"): "
        "nesse caso, reaproveite os filtros do plano anterior e acrescente ou troque o que a pergunta atual pedir. "
        "Se a pergunta atual for independente, ignore esta seção.\n"
        f"Pergunta anterior: {anterior.pergunta}\nPlano anterior: {json.dumps(plano, ensure_ascii=False)}\n{lista}\n"
        f"Pergunta atual: {pergunta}"
    )


def analisar(pergunta: str, ctx: Contexto, anterior: TurnoAnterior | None = None) -> PlanoConsulta:
    """Pede o plano à LLM; se vier inválido, devolve a ela a lista de problemas uma vez."""
    sistema = montar_prompt_analise(ctx)
    base = mensagem_do_analisador(pergunta, anterior)
    usuario = base
    ultimo = ""
    for tentativa in range(1, TENTATIVAS_DO_ANALISADOR + 1):
        try:
            with ctx.logger.etapa(Stage.QUERY_ANALYSIS, pergunta) as e:
                c = ctx.llm.completar(sistema, usuario, json=True)
                e.modelo, e.output = c.modelo, c.texto
                e.tokens.somar(c.tokens)
                ctx.tokens.somar(c.tokens)
                e.extra = {"tentativa": tentativa}
        except PedidoRecusado as erro:  # o servidor não aceitou (na Groq, JSON inválido no modo JSON): pede de novo, sem derrubar a pergunta
            ultimo = str(erro)
            usuario = f"{base}\n\nO servidor recusou a sua resposta anterior (provavelmente por JSON inválido). Responda só com o JSON do plano."
            continue
        try:
            plano = PlanoConsulta.model_validate(_extrair_json(c.texto))
            validar_plano(plano, ctx.cap)
            _conferir_disponibilidade(plano, ctx)
            _conferir_valores(plano, ctx)
            if tentativa < TENTATIVAS_DO_ANALISADOR:  # na última tentativa o plano é aceito, para não travar a pergunta
                _conferir_indicador(pergunta, plano, ctx)
            return plano
        except (ValueError, ValidationError, PlanoInvalido) as erro:
            ultimo = "; ".join(erro.problemas) if isinstance(erro, PlanoInvalido) else str(erro).replace("\n", " ")
            if "existe um indicador pronto" not in ultimo:
                ultimo += _dica_de_indicador(pergunta, ctx)
            usuario = f"{base}\n\nSua resposta anterior foi recusada pelo validador: {ultimo}\nCorrija e responda só com o JSON."
    reserva = plano_de_reserva(pergunta, ctx)  # o analisador não acertou: se há um indicador inequívoco, usa-o sem o modelo
    if reserva is not None:
        with ctx.logger.etapa(Stage.QUERY_ANALYSIS, pergunta) as e:
            e.output = reserva.model_dump_json()
            e.extra = {"plano_de_reserva": True}
        return reserva
    raise PerguntaNaoEntendida(ultimo)


def indicadores_sugeridos(pergunta: str, ctx: Contexto) -> list[str]:
    """Indicadores prontos cujo assunto a pergunta cita, quando ela pede um número calculado."""
    catalogo = ctx.cap.catalogo_de_indicadores
    if catalogo is None or ctx.banco_agregado is None:
        return []
    q = _sem_acento(pergunta)
    if not any(c in q for c in INTENCAO_AGREGADA):
        return []
    return [i for i, ind in catalogo.indicadores.items() if any(k in q for k in ind.palavras_chave)]


def _dica_de_indicador(pergunta: str, ctx: Contexto) -> str:
    """O que dizer ao analisador quando existe um indicador para a pergunta: o id e um plano de exemplo pronto."""
    sugeridos = indicadores_sugeridos(pergunta, ctx)
    if not sugeridos:
        return ""
    catalogo = ctx.cap.catalogo_de_indicadores.indicadores
    exemplo = next(((i, f) for i in sugeridos for _, f in catalogo[i].exemplos[:1]), None)
    texto = (f" Existe um indicador pronto para esta pergunta: {', '.join(sugeridos)}. Use a rota agregada, com naturezas [\"agregada\"] e entidade igual ao id "
             "do indicador: ele traz a definição, o recorte e as ressalvas. Não use uma contagem sobre a tabela de dados.")
    if exemplo:
        plano_pronto = {"rota": "agregada", "naturezas": ["agregada"], "entidade": exemplo[0], "filtros": exemplo[1], "operacoes": [],
                        "campo_agregacao": None, "agrupar_por": [], "limite": None, "consulta_semantica": None}
        texto += f" Plano de exemplo, em que você só ajusta os filtros (use apenas dimensões do indicador): {json.dumps(plano_pronto, ensure_ascii=False)}"
    return texto


_ANO_NA_PERGUNTA = re.compile(r"\b(20\d{2})\b")


def plano_de_reserva(pergunta: str, ctx: Contexto) -> PlanoConsulta | None:
    """Plano montado sem o modelo, para quando o analisador esgota as tentativas e há um indicador pronto para a pergunta.

    É deliberadamente estreito, para nunca responder um recorte diferente do pedido: só vale quando (1) um único indicador
    sem dimensão extra (além do ano) cobre o assunto, (2) nenhum indicador mais específico foi pedido (as palavras de recorte
    dele não aparecem na pergunta, e todo indicador com dimensão extra declara essas palavras) e (3) a pergunta cita no máximo um ano.
    """
    sugeridos = indicadores_sugeridos(pergunta, ctx)
    if not sugeridos:
        return None
    catalogo = ctx.cap.catalogo_de_indicadores.indicadores
    q = _sem_acento(pergunta)
    simples = []
    for i in sugeridos:
        ind = catalogo[i]
        if all(d.startswith("ano") for d in ind.dimensoes):
            simples.append(ind)
        elif not ind.palavras_de_recorte or any(p in q for p in ind.palavras_de_recorte):
            return None  # o recorte específico foi pedido, ou não dá para saber: melhor não responder do que responder outro recorte
    anos = set(_ANO_NA_PERGUNTA.findall(q))
    if len(simples) != 1 or len(anos) > 1:
        return None
    ind = simples[0]
    filtros = {d: int(a) for d in ind.dimensoes for a in anos}
    plano = PlanoConsulta(rota=Rota.AGREGADA, naturezas=[Natureza.AGREGADA], entidade=ind.id, filtros=filtros)
    try:
        validar_plano(plano, ctx.cap)
        _conferir_disponibilidade(plano, ctx)
        _conferir_valores(plano, ctx)
    except PlanoInvalido:
        return None
    return plano


def _conferir_indicador(pergunta: str, plano: PlanoConsulta, ctx: Contexto) -> None:
    """Se existe um indicador para o que se pede, o plano tem de usá-lo (ele traz definição, recorte e ressalvas)."""
    sugeridos = indicadores_sugeridos(pergunta, ctx)
    if sugeridos and not (plano.entidade in sugeridos and usa_base_agregada(plano, ctx.cap)):
        raise PlanoInvalido([_dica_de_indicador(pergunta, ctx).strip().replace("Existe um indicador pronto", "existe um indicador pronto", 1)])


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
    faltam = [n for n in plano.naturezas if n in (Natureza.ESTRUTURADA, Natureza.AGREGADA, Natureza.NORMATIVA, Natureza.CONVERSACIONAL) and n not in ctx.naturezas]
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


def evidencia_agregada(r: ResultadoSQL, ficha: dict[str, Any], numero: int) -> Evidencia:
    """Um indicador chega ao modelo com a definição, o recorte e as ressalvas, e não só com o número."""
    unidade = {"proporcao": "proporção, como fração entre 0 e 1 (0,5 = 50%); numerador e denominador são as contagens usadas",
               "reais": "reais", "media_reais": "média em reais", "contagem": "contagem", "media": "média"}.get(ficha["unidade"], ficha["unidade"])
    base = ficha["calculado_sobre"]
    periodo = base.get("periodo_de_publicacao") or {}
    linhas = [f"Indicador: {ficha['nome']}.", f"Definição: {ficha['definicao']}", f"Unidade: {unidade}.",
              f"Recorte: por {', '.join(ficha['dimensoes'])}"
              + (f"; sempre restrito a: {'; '.join(ficha['filtro_fixo'])}" if ficha["filtro_fixo"] else "")
              + f". Calculado sobre a base {ficha['versao']}"
              + (f", com publicações de {periodo['de']} a {periodo['ate']}." if periodo.get("de") else ".")]
    if ficha["ressalvas"]:
        linhas.append("Ressalvas: " + " ".join(ficha["ressalvas"]))
    linhas.append(f"{len(r.linhas)} linha(s) retornada(s) para: {r.descricao}.")
    for a in r.avisos:
        linhas.append(f"ATENÇÃO: {a}")
    for l in r.linhas[:MAX_LINHAS_NA_EVIDENCIA]:
        linhas.append("; ".join(f"{k}={_valor(v)}" for k, v in l.items()))
    chave = hashlib.sha256((r.sql + json.dumps(r.parametros, default=str)).encode()).hexdigest()[:10]
    return Evidencia(numero, f"ind:{ficha['id']}:{chave}", "base_agregada", f"Indicador: {ficha['nome']} (base agregada {ficha['versao']})", "\n".join(linhas))


def evidencias_documentos(resultados: list, primeiro: int) -> list[Evidencia]:
    saida = []
    for i, r in enumerate(resultados):
        m = r.metadata
        rotulo = " — ".join(x for x in (m.get("norma"), m.get("rotulo")) if x) or m.get("source_file", r.chunk_id)
        if m.get("sintetico"):
            rotulo = f"{m.get('norma') or rotulo} (documento sintético de {m.get('data_publicacao') or 's/d'})"
        elif m.get("versao") and m.get("data_publicacao"):  # versão de protocolo: a data decide qual vale
            fim = m.get("data_vigencia_fim")
            rotulo = f"{rotulo} [publicada em {m['data_publicacao']}; " + (f"vigência encerrada em {fim}" if fim else "sem data de fim de vigência no corpus") + "]"
        saida.append(Evidencia(primeiro + i, r.chunk_id, m.get("base_id", "?"), rotulo, r.texto[:MAX_CARACTERES_DO_TRECHO]))
    return saida


def _tabela_de_comparacao_de_versoes(plano: PlanoConsulta, ctx: Contexto) -> bool:
    """A entidade do plano compara duas versões de um documento (tem os campos versao_anterior e versao_nova)."""
    entidade = ctx.cap.entidades.get(plano.entidade or "")
    return entidade is not None and {"versao_anterior", "versao_nova"} <= set(entidade.campos)


def completar_plano(plano: PlanoConsulta, ctx: Contexto) -> PlanoConsulta:
    """Numa pergunta de comparação de versões, os documentos têm de vir de todas as versões e da mesma doença.

    O analisador às vezes esquece o `vigente_em: "todas"`, e então a busca de texto traz só a versão em vigor e a resposta
    diz que não há versão anterior. Só se aplica quando o plano já consulta a tabela de comparação e usa documentos, e nunca
    troca um `vigente_em` que o analisador tenha escolhido (uma data, por exemplo).
    """
    if plano.rota not in (Rota.HIBRIDA, Rota.DOCUMENTOS) or not _tabela_de_comparacao_de_versoes(plano, ctx):
        return plano
    docs = dict(plano.filtros_documentos)
    if "vigente_em" not in docs:
        docs["vigente_em"] = "todas"
    for chave, valor in plano.filtros.items():  # o recorte do plano estruturado (a doença) vale também para os documentos
        base = chave.split("__")[0]
        if base in ctx.cap.filtros_de_documentos and base not in docs and isinstance(valor, str):
            docs[base] = valor
    return plano if docs == plano.filtros_documentos else plano.model_copy(update={"filtros_documentos": docs})


def relaxar_igualdades(plano: PlanoConsulta, ctx: Contexto, agregada: bool = False) -> PlanoConsulta | None:
    """Plano com a igualdade em campo de texto trocada por busca de trecho, ou None se não há o que trocar.

    Serve para quando a consulta com igualdade volta vazia: o analisador escreve, por exemplo, versao_nova = "2026", mas o
    campo guarda o nome inteiro da portaria ("Portaria Conjunta SAES/SCTIE 33/2026"). Campos com lista de valores
    conhecidos ou com código não entram, porque o valor deles é conferido antes. Com `agregada`, a entidade é um indicador da
    base agregada (por exemplo, o financiador "Carlos Chagas Filho" no lugar do nome inteiro).
    """
    entidades = ctx.cap.entidades_agregadas() if agregada else ctx.cap.entidades
    entidade = entidades.get(plano.entidade or "")
    if entidade is None:
        return None
    novos: dict[str, Any] = {}
    mudou = False
    for chave, valor in plano.filtros.items():
        campo = entidade.campos.get(chave)
        if campo is not None and campo.tipo in ("texto", "categoria") and not campo.valores and isinstance(valor, str) and f"{chave}__contem" not in plano.filtros:
            novos[f"{chave}__contem"] = valor
            mudou = True
        else:
            novos[chave] = valor
    return plano.model_copy(update={"filtros": novos}) if mudou else None


def restricao_do_turno(plano: PlanoConsulta, ctx: Contexto, anterior: TurnoAnterior | None, avisos: list[str]) -> tuple[str, list[str]] | None:
    """(campo, chaves) que limitam a consulta ao resultado do turno anterior, quando o plano diz "desses" e há como ligar as tabelas."""
    if not plano.do_resultado_anterior or not plano.entidade:
        return None
    if anterior is None or anterior.chaves is None or not anterior.chaves.valores:
        avisos.append("A pergunta se refere ao resultado anterior, mas não há uma listagem anterior com identificadores; a consulta NÃO foi limitada a ele.")
        return None
    campo = ctx.cap.campo_de_ligacao(plano.entidade, anterior.chaves.tipo)
    if campo is None:
        avisos.append(f"A pergunta se refere ao resultado anterior, mas a tabela {plano.entidade} não se liga a ele; a consulta NÃO foi limitada a ele.")
        return None
    return campo, list(anterior.chaves.valores)


def chaves_da_listagem(r: ResultadoSQL, plano: PlanoConsulta, ctx: Contexto) -> ChavesDoResultado | None:
    """As chaves das linhas de uma listagem (sem contar, somar ou agrupar), na ordem em que apareceram e sem repetir."""
    ent = ctx.cap.entidades.get(plano.entidade or "")
    tipo = ctx.cap.tipo_da_chave(plano.entidade or "")
    if ent is None or tipo is None or ent.chave is None or plano.agrupar_por or [o for o in plano.operacoes if o != "listar"]:
        return None
    vistos = dict.fromkeys(str(l[ent.chave]) for l in r.linhas if l.get(ent.chave) is not None)
    return ChavesDoResultado(tipo=tipo, valores=tuple(vistos)) if vistos else None


def coletar(plano: PlanoConsulta, pergunta: str, ctx: Contexto, anterior: TurnoAnterior | None = None) -> tuple[list[Evidencia], list[str]]:
    """Executa as partes do plano. Devolve as evidências e avisos sobre o que não deu para consultar."""
    plano = completar_plano(plano, ctx)
    evidencias: list[Evidencia] = []
    avisos: list[str] = []
    usa_agregada = bool(plano.entidade) and ctx.banco_agregado is not None and usa_base_agregada(plano, ctx.cap)
    usa_sql = plano.rota in (Rota.ESTRUTURADA, Rota.HIBRIDA) and plano.entidade and ctx.banco and not usa_agregada
    usa_docs = plano.rota in (Rota.DOCUMENTOS, Rota.HIBRIDA) and ctx.indices

    if usa_agregada:
        with ctx.logger.etapa(Stage.ROTEAMENTO, plano.model_dump_json()) as e:
            try:
                r = consultar(plano, ctx.banco_agregado, ctx.cap)
                ficha = ler_ficha(ctx.banco_agregado, plano.entidade)
                relaxado = relaxar_igualdades(plano, ctx, agregada=True) if not r.linhas else None
                r2 = consultar(relaxado, ctx.banco_agregado, ctx.cap) if relaxado is not None else None
            except sqlite3.Error as erro:
                raise RuntimeError(f"A base agregada não pôde ser consultada: {erro}") from erro
            ampliado = False
            if r2 is not None and r2.linhas:  # a consulta exata voltou vazia: usa a de busca por trecho, se ela achou linhas
                r, ampliado = r2, True
            e.output = f"{len(r.linhas)} linhas (indicador {plano.entidade})"
            e.extra = {"sql": r.sql, "parametros": [str(p) for p in r.parametros], "indicador": plano.entidade, **({"filtro_ampliado": True} if ampliado else {})}
        evidencias.append(evidencia_agregada(r, ficha, len(evidencias) + 1))
    if usa_sql:
        restricao = restricao_do_turno(plano, ctx, anterior, avisos)
        with ctx.logger.etapa(Stage.ROTEAMENTO, plano.model_dump_json()) as e:
            try:
                r = consultar(plano, ctx.banco, ctx.cap, restricao=restricao)
            except sqlite3.Error as erro:
                raise RuntimeError(f"A base estruturada não pôde ser consultada: {erro}") from erro
            relaxado = relaxar_igualdades(plano, ctx) if not r.linhas else None
            if relaxado is not None:  # a consulta exata voltou vazia: tenta de novo com busca por trecho nos campos de texto
                try:
                    r2 = consultar(relaxado, ctx.banco, ctx.cap, restricao=restricao)
                except sqlite3.Error as erro:
                    raise RuntimeError(f"A base estruturada não pôde ser consultada: {erro}") from erro
                if r2.linhas:
                    r = r2
                    e.extra = {"filtro_ampliado": True}
            e.output = f"{len(r.linhas)} linhas"
            e.extra = {**(e.extra or {}), "sql": r.sql, "parametros": [str(p) for p in r.parametros], "truncado": r.truncado}
        evidencia = evidencia_sql(r, len(evidencias) + 1)
        evidencia.chaves = chaves_da_listagem(r, plano, ctx)
        evidencias.append(evidencia)
    if usa_docs:
        consulta = plano.consulta_semantica or pergunta
        filtros_docs = filtros_de_documentos(plano, ctx)
        alvo = {n for n in plano.naturezas if n in (Natureza.NORMATIVA, Natureza.CONVERSACIONAL)} or {Natureza.NORMATIVA}
        indices = {b: i for b, i in ctx.indices.items() if ctx.natureza_da_base(b) in alvo}
        achados = []
        if indices and "vigente_em" not in filtros_docs and plano.filtros_documentos.get("vigente_em") == "todas":
            # comparar versões: um trecho de cada versão basta (a tabela de comparação já traz o detalhe), e mais que isso passa do
            # limite de 8.000 tokens por pedido do plano gratuito da Groq quando o protocolo tem cinco versões
            achados = buscar_por_versao(indices, ctx.embedder, consulta, filtros=filtros_docs, por_versao=1, logger=ctx.logger, reranker=ctx.reranker, n_rerank=ctx.rerank_candidatos)
        if indices and not achados:
            achados = buscar_por_bases(indices, ctx.embedder, consulta, k=5, filtros=filtros_docs, logger=ctx.logger, reranker=ctx.reranker, n_rerank=ctx.rerank_candidatos)
        evidencias += evidencias_documentos(achados, len(evidencias) + 1)
        if not achados:
            avisos.append("Nenhum trecho de documento foi encontrado para a pergunta.")
    return evidencias, avisos


def filtros_de_documentos(plano: PlanoConsulta, ctx: Contexto) -> dict[str, Any]:
    """Filtros da busca em documentos: o nível de sensibilidade, a vigência e os campos do cenário que o plano pede.

    Sem `vigente_em` no plano vale a data de hoje, isto é, só a versão em vigor; "todas" tira o corte de vigência
    (para comparar versões ou perguntar por uma anterior).
    """
    filtros: dict[str, Any] = {"sensitivity": list(ctx.cap.niveis_consultaveis)}
    vigencia = plano.filtros_documentos.get("vigente_em", ctx.hoje)
    if vigencia != "todas":
        filtros["vigente_em"] = vigencia
    for chave, valor in plano.filtros_documentos.items():
        if chave != "vigente_em" and chave in ctx.cap.filtros_de_documentos:
            filtros[f"{chave}__contem"] = valor
    return filtros


# --------------------------------------------------------------------------- geração e validação

def sistema_geracao(cap: Capacidades) -> str:
    regras = [
        "Cite a evidência com [n] logo depois de cada afirmação, por exemplo \"Foram 896 dispensas [1].\" Nunca cite um número que não existe.",
        "Use apenas fatos e números das evidências. Não complete com conhecimento próprio. Se a evidência não basta, diga o que falta.",
        *cap.regras_de_resposta,
        "Se a consulta não devolveu registros e a evidência avisa que o filtro pode não ter casado, diga isso; nunca afirme que \"não há\" ou que \"nenhum\" registro existe.",
        "Não tire conclusões que as evidências não sustentam (causa, mérito, alinhamento, relação entre dois conjuntos de dados). "
        "Se a pergunta pede isso, diga o que os dados mostram e o que eles não mostram. Não responda apenas \"sim\" ou \"não\" nesses casos.",
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


def gerar(pergunta: str, evidencias: list[Evidencia], avisos: list[str], ctx: Contexto) -> tuple[str, list[Evidencia], list[str]] | None:
    """Escreve a resposta e confere as citações e os números. Uma tentativa extra se citar errado, não citar nada ou trouxer um número
    que a evidência não tem. Devolve também os números que continuaram sem conferência na última tentativa."""
    validos = {e.numero for e in evidencias}
    textos_da_evidencia = [f"{e.rotulo or ''} {e.texto}" for e in evidencias]
    correcao = ""
    texto = ""
    suspeitos: list[str] = []
    for tentativa in range(1, 3):
        while True:
            try:
                with ctx.logger.etapa(Stage.GERACAO, pergunta) as e:
                    c = ctx.llm.completar(sistema_geracao(ctx.cap), _prompt_geracao(pergunta, evidencias, avisos, correcao))
                    e.modelo, e.output = c.modelo, c.texto
                    e.tokens.somar(c.tokens)
                    ctx.tokens.somar(c.tokens)
                    e.extra = {"tentativa": tentativa, "evidencias": len(evidencias)}
                break
            except PedidoGrandeDemais:
                if len(evidencias) <= 1:
                    raise  # não há mais o que cortar
                # O provedor recusou o tamanho: tira a última evidência (a de menor posição na busca, ou a versão mais antiga) e avisa o modelo.
                # A numeração das que ficam não muda, então as citações continuam valendo.
                evidencias = evidencias[:-1]
                validos = {e.numero for e in evidencias}
                textos_da_evidencia = textos_da_evidencia[: len(evidencias)]
                if AVISO_EVIDENCIA_CORTADA not in avisos:
                    avisos = [*avisos, AVISO_EVIDENCIA_CORTADA]
        texto = normalizar_citacoes(c.texto).strip()
        citados = _citados(texto)
        ruins = sorted(set(citados) - validos)
        # Os números se conferem no texto já mascarado: o aviso nunca repete um CPF ou telefone que a resposta final esconde.
        suspeitos = numeros_nao_conferidos(mascarar_dados_pessoais(texto), textos_da_evidencia, pergunta) if citados and not ruins else []
        with ctx.logger.etapa(Stage.VALIDACAO, texto) as v:
            v.output = f"citadas={sorted(set(citados))} invalidas={ruins} numeros_nao_conferidos={suspeitos}"
        if citados and not ruins and not suspeitos:
            break
        if citados and not ruins:  # citações certas, mas há número sem apoio na evidência
            correcao = (
                f"Na sua resposta anterior, estes números não aparecem na evidência: {', '.join(suspeitos)}. Refaça usando só números que estejam "
                "na evidência, copiados como estão; se uma conta for necessária e a evidência não a traz, diga que não dá para calcular."
            )
            continue
        correcao = (
            f"Sua resposta anterior citou fontes que não existem: {ruins}. " if ruins else "Sua resposta anterior não citou nenhuma evidência. "
        ) + f"Refaça citando apenas {sorted(validos)} com o formato [n]."
    texto = _tirar_citacoes_invalidas(texto, validos)
    usadas = sorted(set(_citados(texto)))
    if not usadas:
        return None
    return mascarar_dados_pessoais(texto), [e for e in evidencias if e.numero in usadas], suspeitos


# --------------------------------------------------------------------------- pipeline


def _recusa(motivo: str, mensagem: str, ctx: Contexto, rotas: list[Rota] | None = None, plano: PlanoConsulta | None = None) -> RAGResponse:
    return RAGResponse(answer=mensagem, is_refusal=True, refusal_reason=motivo, rotas=rotas or [], trace_id=ctx.logger.query_id, plano=plano)


def _resposta_catalogo(ctx: Contexto) -> str:
    cat = ctx.cap.catalogo
    partes = ["Posso consultar:"]
    if Natureza.ESTRUTURADA in ctx.naturezas and cat.get("estruturada"):
        partes.append(f"- {cat['estruturada']};")
    if Natureza.NORMATIVA in ctx.naturezas and cat.get("normativa"):
        partes.append(f"- {cat['normativa']};")
    if Natureza.CONVERSACIONAL in ctx.naturezas and cat.get("conversacional"):
        partes.append(f"- {cat['conversacional']}.")
    if ctx.cobertura:
        partes.append(f"Cobertura: {ctx.cobertura}")
    if Natureza.AGREGADA in ctx.naturezas:
        ind = ctx.cap.catalogo_de_indicadores
        partes.append("Indicadores prontos: " + "; ".join(i.nome for i in ind.indicadores.values()) + ".")
        if ind.limites:
            partes.append("O que não calculo: " + " ".join(ind.limites))
    if cat.get("aviso"):
        partes.append(cat["aviso"])
    return "\n".join(partes)


def responder(pergunta: str, ctx: Contexto, anterior: TurnoAnterior | None = None) -> RAGResponse:
    with ctx.logger.etapa(Stage.GUARDRAIL, pergunta) as e:
        decisao = avaliar_pergunta(pergunta, ctx.cap)
        e.output = "permitida" if decisao.permitida else f"recusada: {decisao.categoria}"
    if not decisao.permitida:
        return _recusa(decisao.categoria or "recusada", decisao.mensagem or "Não posso responder a essa pergunta.", ctx)

    if not ctx.naturezas:
        return _recusa("sem_dados", "Nenhuma base está carregada neste ambiente. Rode a coleta, a normalização e a indexação do cenário.", ctx)

    try:
        plano = analisar(pergunta, ctx, anterior)
    except PerguntaNaoEntendida:
        return _recusa("pergunta_nao_entendida", "Não consegui transformar a pergunta em uma consulta que eu saiba fazer. Tente reformular, citando o órgão, o período ou o tipo de contratação.", ctx)

    if plano.rota is Rota.FORA_DO_ESCOPO:
        return _recusa("fora_do_escopo", "Não consigo responder a isso com as bases que tenho.\n" + _resposta_catalogo(ctx), ctx, [Rota.FORA_DO_ESCOPO], plano)

    if plano.rota is Rota.CATALOGO:
        return RAGResponse(answer=_resposta_catalogo(ctx), rotas=[Rota.CATALOGO], cobertura=ctx.cobertura, trace_id=ctx.logger.query_id, plano=plano)

    evidencias, avisos = coletar(plano, pergunta, ctx, anterior)
    if not evidencias:
        return _recusa("sem_evidencia", "Não encontrei nas bases disponíveis informação para responder a essa pergunta.", ctx, [plano.rota], plano)

    gerado = gerar(pergunta, evidencias, avisos, ctx)
    if gerado is None:
        return _recusa("sem_fonte_verificavel", "Não consegui escrever uma resposta com fontes verificáveis. Tente reformular a pergunta.", ctx, [plano.rota])
    texto, usadas, nao_conferidos = gerado
    with ctx.logger.etapa(Stage.VALIDACAO, "guarda de saída") as v:
        saida = avaliar_resposta(texto, ctx.cap)
        v.output = "liberada" if saida.permitida else f"bloqueada: {saida.categoria}"
    if not saida.permitida:
        return _recusa(saida.categoria or "saida_bloqueada", saida.mensagem or "A resposta foi bloqueada.", ctx, [plano.rota], plano)
    return RAGResponse(
        answer=texto,
        sources_used=[Fonte(numero=e.numero, chunk_id=e.chunk_id, base_id=e.base_id, rotulo=e.rotulo) for e in usadas],
        rotas=[plano.rota],
        cobertura=ctx.cobertura,
        trace_id=ctx.logger.query_id,
        plano=plano,
        numeros_nao_conferidos=nao_conferidos,
        chaves_do_resultado=next((e.chaves for e in evidencias if e.chaves), None),
    )


# --------------------------------------------------------------------------- montagem do ambiente real


def listar_valores_existentes(banco: Path, cap: Capacidades, limite: int = 40, entidades: dict | None = None) -> dict[str, dict[str, list[str]]]:
    """Valores distintos dos campos marcados com `listar_valores`, se forem poucos (até `limite`)."""
    achados: dict[str, dict[str, list[str]]] = {}
    conexao = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)
    try:
        for nome, ent in (entidades if entidades is not None else cap.entidades).items():
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


def _naturezas_do_manifesto(pastas: Pastas, indices: dict[str, IndiceCarregado]) -> dict[str, Natureza]:
    """base_id -> natureza, lida do manifesto do cenário (uma base sem manifesto é tratada como normativa)."""
    caminho = pastas.cenario / "corpus_manifest.yaml"
    if not caminho.is_file():
        return {}
    import yaml

    bases = (yaml.safe_load(caminho.read_text(encoding="utf-8")) or {}).get("bases") or []
    return {b["base_id"]: Natureza(b["natureza"]) for b in bases if b.get("base_id") in indices and b.get("natureza")}


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
    naturezas = _naturezas_do_manifesto(pastas, indices)
    banco_agregado = None
    if cap.catalogo_de_indicadores is not None:
        try:
            banco_agregado = localizar_agregado(pastas)
            conferir_atualizacao(banco_agregado, pastas.cenario_id)
        except FileNotFoundError:
            banco_agregado = None
    valores = listar_valores_existentes(banco, cap) if banco is not None else {}
    if banco_agregado is not None:
        valores.update(listar_valores_existentes(banco_agregado, cap, entidades=cap.entidades_agregadas()))
    reranker = None
    if config.rerank:
        try:
            reranker = carregar_reranker(max_tokens=config.rerank_max_tokens)
        except RerankerIndisponivel as erro:
            print(f"Aviso: rerank ligado, mas indisponível ({erro}); seguindo sem ele.", file=sys.stderr)
    return Contexto(llm=criar_llm(config), logger=logger, cap=cap, banco=banco, banco_agregado=banco_agregado, indices=indices, embedder=embedder, reranker=reranker, rerank_candidatos=config.rerank_candidatos,
                    cobertura=cobertura, bases=sorted(indices), natureza_das_bases=naturezas, valores_conhecidos=valores)


def formatar(resposta: RAGResponse) -> str:
    saida = [resposta.answer]
    if resposta.sources_used:
        saida += ["", "Fontes:"] + [f"  [{f.numero}] {f.rotulo or f.chunk_id}" for f in resposta.sources_used]
    if resposta.cobertura and not resposta.is_refusal:
        saida.append(f"\nCobertura: {resposta.cobertura}")
    if resposta.numeros_nao_conferidos:
        saida.append(f"\nAtenção: estes números da resposta não foram encontrados na evidência: {', '.join(resposta.numeros_nao_conferidos)}. Confira nas fontes.")
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

    anterior: TurnoAnterior | None = None  # só o modo interativo lembra o turno anterior

    def perguntar(texto: str, com_contexto: bool = False) -> int:
        nonlocal anterior
        ctx.logger.query_id = f"q_{hashlib.sha256(texto.encode()).hexdigest()[:6]}"
        try:
            r = responder(texto, ctx, anterior if com_contexto else None)
        except (LLMIndisponivel, EmbeddingIndisponivel, RuntimeError) as erro:
            print(f"Erro: {erro}", file=sys.stderr)
            return 3
        anterior = TurnoAnterior(texto, r.plano, r.chaves_do_resultado) if (com_contexto is not None and not r.is_refusal and r.plano is not None) else None
        print(r.model_dump_json(indent=2) if args.json else formatar(r))
        return 0

    if args.pergunta:
        return perguntar(" ".join(args.pergunta))
    print("Faça uma pergunta (linha vazia para sair). Perguntas de continuação, como \"desses, quais...\", usam a pergunta anterior; \"/nova\" começa um assunto novo.")
    while True:
        try:
            texto = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if not texto:
            return 0
        if texto == "/nova":
            anterior = None
            continue
        perguntar(texto, com_contexto=True)


if __name__ == "__main__":
    raise SystemExit(main())
