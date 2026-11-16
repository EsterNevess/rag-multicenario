# app

Interface de perguntas em uma tela, feita com Streamlit.

```bash
pip install -r requirements-app.txt
streamlit run app/streamlit_app.py
```

O que ela faz: escolha o cenário (RJ, saúde ou pesquisa), faça a pergunta ou clique num exemplo, e veja a resposta com as fontes numeradas e a
cobertura dos dados. O painel "Como o sistema decidiu" mostra a rota e o plano da consulta que o modelo montou (o código é quem o valida e
executa; o modelo nunca escreve SQL). Recusas aparecem como aviso, com o motivo, e números da resposta que não estão na evidência aparecem como um aviso à parte. Se o provedor do modelo falhar (por exemplo, o limite do plano
gratuito da Groq), a tela mostra uma mensagem em vez de um erro.

**Continuar a conversa.** A caixa "Continuar a conversa" (ligada por padrão) manda ao analisador a pergunta anterior e o plano que ela gerou, para resolver "desses, quais..." ou "e em 2025?". A memória é de **um turno**, não inclui o texto da resposta (ele traz trechos de documentos, e um documento não deve poder dar instruções ao analisador), e só vale para respostas que não foram recusa. Trocar de cenário a apaga. Funciona também no modo interativo da linha de comando (`/nova` começa um assunto novo). Está testada com um modelo simulado; **não foi conferida com o modelo real**.

O que ela **não** faz: login, histórico entre sessões nem API HTTP. Os dados são os que o
cenário tem carregados em `data/` (veja o README da raiz para coletar, ingerir e indexar), e o modelo é o do `.env`.

- `nucleo.py`: a lógica, sem Streamlit (criar o contexto, perguntar, formatar o plano e as fontes).
- `streamlit_app.py`: a tela.
- Testes: `tests/unit/test_app.py` (a lógica e a tela, com o `AppTest` do Streamlit e o motor trocado por um falso, sem navegador).
