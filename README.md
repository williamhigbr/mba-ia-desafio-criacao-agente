# Assistente virtual do Residencial Aurora

API em Python (FastAPI + Google ADK 2.11.0 + Gemini) com o assistente do Residencial Aurora. Pelo chat, o morador reserva e cancela áreas comuns, autoriza visitantes e tira dúvidas sobre o regulamento.

O modelo conduz a conversa, mas as regras críticas estão no código: confirmação de cobrança e de acesso, apartamento da sessão, persistência, regulamento fora do histórico e exclusividade da reserva. Elas continuam valendo seja qual for o texto que o morador escrever.

## Arquitetura

```
POST /sessoes/...  ──►  FastAPI  ──►  Runner (App resumível)  ──►  assistente_aurora (root, só roteia)
(aurora/api)             │                                          ├── especialista_reservas    (sub-agente, transferência)
                         │                                          ├── especialista_visitantes  (sub-agente, transferência)
                         │                                          └── especialista_regulamento (AgentTool, sessão isolada)
                         │
                         ├── SqliteSessionService → var/sessoes.db      (sessões e eventos do ADK)
                         └── repositório SQLite   → var/condominio.db   (reservas, visitantes, sessão → apartamento)
```

### Agentes

| Agente | Arquivo | Responsabilidade | Como é acionado | Por quê |
|---|---|---|---|---|
| `assistente_aurora` (root) | `aurora/agents/root.py` | Conversa com o morador e encaminha cada pedido ao especialista certo. Não tem tools de dados nem o regulamento. | Pelo `Runner`, a cada mensagem | Um ponto de entrada só. Como ele não lê nem grava nada, não há como convencê-lo a furar uma regra de dados. |
| `especialista_reservas` | `aurora/agents/reservas.py` | Consulta disponibilidade, reserva, cancela e lista as reservas do morador | `sub_agents` do root, por `transfer_to_agent` | Precisa conversar (perguntar a data, por exemplo) e pedir confirmação de cobrança. Como sub-agente em modo chat, os eventos dele ficam na sessão, e o Runner devolve a resposta da confirmação a ele. |
| `especialista_visitantes` | `aurora/agents/visitantes.py` | Autoriza a entrada de visitantes e lista as autorizações do morador | `sub_agents` do root, por `transfer_to_agent` | Mesmo motivo: toda autorização exige confirmação, e a retomada precisa chegar a este agente. |
| `especialista_regulamento` | `aurora/agents/regulamento.py` | Responde dúvidas lendo só o capítulo do assunto (`ler_capitulo`) | `AgentTool` explícito nas `tools` do root | O `AgentTool` executa o especialista em uma sessão em memória separada. O texto dos capítulos lidos fica lá, e a sessão do morador recebe só a pergunta e a resposta final (Garantia 4). Um sub-agente `mode="single_turn"` rodaria na mesma sessão e gravaria o capítulo nos eventos. |

O `App` (`aurora/agent.py`) é resumível (`ResumabilityConfig(is_resumable=True)`). Assim, quando chega a resposta de uma confirmação, o Runner retoma a invocação no especialista que pediu a confirmação, e não no root, que não tem a tool.

### Tools

Todas as leituras e gravações de reservas e visitantes passam por tools (`aurora/tools/`), que chamam o repositório (`aurora/repositorio.py`). O modelo nunca responde dados de memória.

| Tool | Especialista | Confirmação |
|---|---|---|
| `listar_areas`, `listar_minhas_reservas`, `consultar_disponibilidade`, `cancelar_reserva` | reservas | não |
| `reservar_area` | reservas | só quando a área tem taxa > 0 |
| `listar_meus_visitantes` | visitantes | não |
| `autorizar_visitante` | visitantes | sempre |
| `listar_capitulos`, `ler_capitulo` | regulamento | não |

### Escolhas

- **Modelo:** Gemini, via Google AI Studio, configurável em `AURORA_MODELO`. O padrão é `gemini-flash-latest`, o mesmo para todos os agentes, porque o trabalho de cada um é roteamento ou chamada de tool, não raciocínio longo. O cliente tenta de novo com backoff em 429 e 5xx (`aurora/agents/__init__.py`).
- **Armazenamento:** SQLite em arquivo, sem nenhum serviço externo. Usa dois bancos (`var/sessoes.db` para o ADK e `var/condominio.db` para os dados do condomínio), para que as escritas de um não disputem o lock do outro. Os arquivos de `dados/` são só lidos, pela restauração e pelo catálogo de áreas.
- **Pendências de confirmação:** derivadas dos eventos persistidos da sessão (`aurora/api/confirmacoes.py`), sem nenhum estado paralelo.

## Garantias

### 1. Cobrança ou acesso só com confirmação

- **Reserva com taxa** (`aurora/tools/reservas.py`, `reservar_area`): o código decide pela taxa da área em `dados/areas.json`.
  ```python
  gera_cobranca = info["taxa"] > 0
  confirmacao = tool_context.tool_confirmation
  ...
  if gera_cobranca:
      tool_context.request_confirmation(hint=..., payload={"area": area, "data": data, "taxa": info["taxa"]})
  ```
  A gravação (`repositorio.criar_reserva`) só acontece quando `tool_confirmation` existe e está aprovada. Quando ela está negada, a tool retorna `"negado"` sem gravar. Área com taxa 0 grava direto, sem confirmação.
- **Visitante** (`aurora/tools/visitantes.py`): `FunctionTool(autorizar_visitante, require_confirmation=True)`. O próprio ADK intercepta a chamada, e a função só roda depois da aprovação.
- **Rota de confirmações** (`aurora/api/main.py`, `responder_confirmacao`): antes de chamar o Runner, ela recalcula as pendências da sessão a partir dos eventos. Um `id` que não está pendente nesta sessão recebe `409` e nada executa. Isso vale para um id inexistente, para o de outra sessão e para o de uma confirmação já respondida.
  ```python
  pendentes = {p.id: p for p in confirmacoes_pendentes(sessao.events)}
  pendencia = pendentes.get(corpo.id)
  if pendencia is None:
      raise HTTPException(status_code=409, ...)
  ```
- **Retomada** (`aurora/api/execucao.py`, `responder_confirmacao`): monta o `FunctionResponse` de `adk_request_confirmation` com `{"confirmed": ...}` e chama `run_async` com o `invocation_id` da pendência.
- **Execução única:** `criar_reserva` e `autorizar_visitante` usam o `function_call_id` como chave de idempotência (coluna `chave_idempotencia UNIQUE` em `aurora/db.py`), então uma reexecução da mesma chamada não grava de novo.

**Por que não depende do modelo:** `tool_context.tool_confirmation` só existe quando a API devolve ao ADK a resposta enviada pela rota `/confirmacoes`. Nenhum texto no chat ("já estou confirmando", "confirmed=true") cria esse objeto, e a decisão de pedir confirmação vem da taxa ou do `require_confirmation`, nunca do modelo.

### 2. Cada sessão pertence a um apartamento

- **Fixado uma vez** (`aurora/api/main.py`, `criar_sessao`): o apartamento vira o `user_id` da sessão do ADK (`create_session(app_name=..., user_id=corpo.apartamento)`) e é gravado no mapa `sessoes` (`repositorio.registrar_sessao`). Ele não é copiado para o state, que tools e agentes podem alterar.
- **Fonte única nas tools** (`aurora/tools/_contexto.py`, `apartamento_da_sessao`): `apartamento = tool_context.user_id`, com erro se vier vazio. Nenhuma tool recebe apartamento como parâmetro. Todas as que leem ou gravam dados do morador usam essa função.
- **Queries filtradas pelo apartamento** (`aurora/repositorio.py`): `listar_reservas`, `listar_visitantes` e `cancelar_reserva` filtram por `apartamento = ?`. O cancelamento usa `UPDATE ... WHERE apartamento = ? AND status = 'ativa' AND ...`, então a reserva de outro apartamento dá `"nao_encontrada"`, a mesma resposta de uma reserva que não existe.
- **Data ocupada sem dono** (`aurora/tools/reservas.py`, `_indisponivel` e `consultar_disponibilidade`): o retorno diz só `livre` ou `"indisponivel"`. Só quando a reserva é do próprio morador o retorno traz os dados dela. Código e apartamento de outros nunca chegam ao modelo.

**Por que não depende do modelo:** o modelo não tem nenhum parâmetro em que possa colocar outro apartamento, e as tools não devolvem dados de outro apartamento. Mesmo convencido de que o morador é "do 302", o modelo não tem como ler nem alterar o que é do 302.

### 3. Nada se perde no reinício

- **Sessões e eventos** (`aurora/api/main.py`, `lifespan`): `SqliteSessionService(str(config.SESSOES_DB))`, em `var/sessoes.db`. As rotas releem a sessão do banco a cada requisição.
- **Dados do condomínio** (`aurora/db.py`): SQLite em `var/condominio.db`, com WAL. A API só cria o schema quando ele não existe (`garantir_schema`) e nunca recarrega `dados/` sozinha. Só o comando de restauração faz isso.
- **Confirmações pendentes** (`aurora/api/confirmacoes.py`, `confirmacoes_pendentes`): derivadas dos eventos persistidos, então uma pendência criada antes do reinício pode ser aprovada depois dele.
- **Códigos nunca repetidos** (regra 5, `aurora/db.py` e `aurora/repositorio.py`): `codigo TEXT PRIMARY KEY`, e o cancelamento é lógico (`status = 'cancelada'`). O código de uma reserva cancelada continua no banco, e `criar_reserva` gera outro se houver colisão.

**Por que não depende do modelo:** é persistência em arquivo, e `GET /eventos` só lê, sem gravar nada na sessão.

### 4. O regulamento é consultado, não carregado

- **Root sem regulamento** (`aurora/agents/root.py`, `INSTRUCAO`): a instrução não contém o texto do regulamento nem os títulos dos capítulos. O root só sabe que existe a ferramenta `especialista_regulamento`.
- **Leitura isolada** (`aurora/agents/root.py`, `tools=[AgentTool(agent=criar_especialista_regulamento(modelo_para))]`): o `AgentTool` roda o especialista em uma sessão em memória própria. As chamadas a `ler_capitulo` e o texto retornado ficam nessa sessão. A sessão do morador recebe só o function call `especialista_regulamento` com a pergunta e a resposta curta.
- **Um capítulo por vez** (`aurora/agents/regulamento.py` e `aurora/tools/regulamento.py`, `ler_capitulo`): o especialista conhece só os títulos dos capítulos e lê o do assunto. O parser fica em `aurora/regulamento.py`.

**Por que não depende do modelo:** o isolamento vem da arquitetura do `AgentTool`. Mesmo que o especialista lesse todos os capítulos, o texto não chegaria aos eventos da sessão do morador.

### 5. Dois moradores, uma reserva

- **Exclusividade no banco** (`aurora/db.py`, `SCHEMA`):
  ```sql
  CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa
      ON reservas (area, data) WHERE status = 'ativa'
  ```
  É um índice parcial, para que uma reserva cancelada libere a data.
- **Gravação** (`aurora/repositorio.py`, `criar_reserva`): o `INSERT` roda dentro de `BEGIN IMMEDIATE` (`db.transacao`), com `busy_timeout`. Se o índice recusar, o `sqlite3.IntegrityError` vira `DataOcupada`.
- **Resposta normal ao perdedor** (`aurora/tools/reservas.py`, `reservar_area`): `except DataOcupada: return await _indisponivel(...)`. O especialista diz que a data está ocupada, e a API responde `200`.

**Por que não depende do modelo:** a consulta de disponibilidade antes de pedir a confirmação é só para a conversa. Quem decide é o banco, no instante do `INSERT`. Duas aprovações simultâneas são serializadas pelo lock de escrita, e a segunda viola o índice único.

## Como rodar

### Pré-requisitos

- [uv](https://docs.astral.sh/uv/). Ele instala o Python 3.12 indicado em `.python-version`, se necessário.
- Uma chave do Google AI Studio.
- Nenhum serviço externo: o armazenamento é SQLite em `var/`, criado automaticamente.

### Variáveis do `.env`

```bash
cp .env.example .env
```

| Variável | Obrigatória | Valor |
|---|---|---|
| `GOOGLE_API_KEY` | sim | chave do Google AI Studio |
| `GOOGLE_GENAI_USE_VERTEXAI` | não | `FALSE` (usa o AI Studio) |
| `AURORA_MODELO` | não | modelo Gemini de todos os agentes; vazio usa `gemini-flash-latest` |

### Comandos

```bash
uv sync                 # instala as dependências (versões fixadas no uv.lock)
uv run aurora-restaurar # restaura os dados iniciais (rode com a API parada)
uv run aurora-api       # sobe a API em http://localhost:8000 (Ctrl+C para parar)
```

`aurora-restaurar` recria reservas e visitantes a partir de `dados/reservas.json` e `dados/visitantes.json`. Ele também apaga as sessões (o banco `var/sessoes.db` e o mapa sessão → apartamento). Para reiniciar a API sem perder nada, pare com Ctrl+C e rode `uv run aurora-api` de novo, sem restaurar.

As rotas seguem o contrato do desafio: `POST /sessoes`, `POST /sessoes/{id}/mensagens`, `POST /sessoes/{id}/confirmacoes`, `GET /sessoes/{id}/eventos`, `GET /apartamentos/{n}/reservas` e `GET /apartamentos/{n}/visitantes`. Não há autenticação, por definição do desafio: o apartamento enviado na criação da sessão representa o morador autenticado. As rotas `/apartamentos/...` ficariam atrás de acesso administrativo em produção.

### Verificação (opcional)

```bash
uv run pytest -q                                        # testes sem chave (modelo roteirizado)
uv run python scripts/roteiro_avaliador.py --todas      # passos 1 a 14 do avaliador com o Gemini real
```

O roteiro restaura os dados, sobe e reinicia a API sozinho, então a porta 8000 precisa estar livre. Ele faz algumas dezenas de chamadas ao modelo por variante.
