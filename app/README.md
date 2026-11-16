# app

Interface de perguntas em uma tela, feita com Streamlit.

```bash
pip install -r requirements-app.txt
streamlit run app/streamlit_app.py
```

O que ela faz: escolha o cenário (RJ, saúde ou pesquisa), faça a pergunta ou clique num exemplo, e veja a resposta com as fontes numeradas e a
cobertura dos dados. O painel "Como o sistema decidiu" mostra a rota e o plano da consulta que o modelo montou (o código é quem o valida e
executa; o modelo nunca escreve SQL). Recusas aparecem como aviso, com o motivo. Se o provedor do modelo falhar (por exemplo, o limite do plano
gratuito da Groq), a tela mostra uma mensagem em vez de um erro.

O que ela **não** faz: login, histórico entre sessões, memória de conversa (cada pergunta é independente) nem API HTTP. Os dados são os que o
cenário tem carregados em `data/` (veja o README da raiz para coletar, ingerir e indexar), e o modelo é o do `.env`.

- `nucleo.py`: a lógica, sem Streamlit (criar o contexto, perguntar, formatar o plano e as fontes).
- `streamlit_app.py`: a tela.
- Testes: `tests/unit/test_app.py` (a lógica e a tela, com o `AppTest` do Streamlit e o motor trocado por um falso, sem navegador).
