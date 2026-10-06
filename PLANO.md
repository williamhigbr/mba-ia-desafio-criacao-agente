# Plano de implementação: assistente do Residencial Aurora (Google ADK)

Este plano tem dois objetivos com o mesmo peso: entregar tudo que o avaliador confere e fazer você entender por que cada peça do ADK se comporta como se comporta. Cada fase tem:

- Conceitos: o que estudar antes de codar (doc oficial + código-fonte do ADK).
- Tarefas: o que construir.
- Checkpoint: como provar que funcionou antes de seguir.
- Fixação: perguntas que você deve conseguir responder sem consultar nada. Se não conseguir, volte aos conceitos.

Regra de ouro do desafio, que guia todas as decisões abaixo: o modelo decide o caminho, o código decide o que é permitido. Toda garantia precisa valer mesmo que o LLM erre, invente ou seja manipulado.

---

## Visão geral da arquitetura proposta

```
POST /sessoes/...  ──►  FastAPI  ──►  Runner (App resumível)  ──►  assistente_aurora (root, só roteia)
                         │                                          ├── especialista_reservas   (sub_agent, transfer)
                         │                                          ├── especialista_visitantes (sub_agent, transfer)
                         │                                          └── especialista_regulamento (AgentTool, contexto isolado)
                         │
                         ├── SqliteSessionService  → var/sessoes.db      (Garantia 3)
                         └── repositório SQLite     → var/condominio.db  (Garantias 2, 5 e regras 1-5)
```

Por que essa topologia:

| Agente | Acionamento | Motivo |
|---|---|---|
| `assistente_aurora` (root) | Runner | Conversa e roteia. Sem tools de dados e sem regulamento nas instruções (Garantia 4). |
| `especialista_reservas` | `sub_agents` + `transfer_to_agent` (modo `chat`) | Precisa conversar com o morador e pedir confirmação. Os eventos dele ficam na sessão, então a confirmação pode ser retomada pelo Runner. |
| `especialista_visitantes` | `sub_agents` + transfer (modo `chat`) | Mesmo motivo: autorização exige confirmação. |
| `especialista_regulamento` | `AgentTool` explícito no root | O `AgentTool` roda o especialista num `InMemorySessionService` próprio (veja `tools/agent_tool.py`). O texto do regulamento lido pelo especialista nunca entra nos eventos da sessão principal; só a resposta final entra. Isso é a Garantia 4 garantida por arquitetura, não por prompt. |

Atenção a uma armadilha do ADK 2.x: um sub_agent com `mode='single_turn'` também vira tool, mas roda via `tool_context.run_node` no mesmo session (num branch). Os eventos dele, inclusive o retorno da tool que lê o regulamento, iriam para `GET /eventos`. Por isso o regulamento usa `AgentTool(agent=...)` explicitamente, e não `mode='single_turn'`. Confirme isso lendo `_SingleTurnAgentTool` e `AgentTool.run_async` em `google/adk/tools/agent_tool.py`.

Layout sugerido do repositório:

```
pyproject.toml / uv.lock / .env.example / README.md
aurora/
  __init__.py
  agent.py              # expõe root_agent e app (permite rodar `adk web`)
  config.py             # caminhos, nome do modelo, APP_NAME
  db.py                 # conexão SQLite, schema, PRAGMAs
  repositorio.py        # funções puras de dados (sem ADK)
  regulamento.py        # parser de capítulos do regulamento.md
  tools/
    reservas.py
    visitantes.py
    regulamento.py
  agents/
    root.py
    reservas.py
    visitantes.py
    regulamento.py
  api/
    main.py             # FastAPI + lifespan + rotas
    confirmacoes.py     # extrair pendências dos eventos
    execucao.py         # rodar o Runner e montar a resposta
  restaurar.py          # comando de restauração
var/                    # bancos SQLite (no .gitignore)
scripts/roteiro_avaliador.sh   # reproduz os 14 passos (ferramenta sua, não é entrega obrigatória)
```

---

## Fase 0: Ambiente e esqueleto do projeto

Conceitos
- uv: `uv init`, `uv add`, `uv sync`, `uv run`, `uv python install`.
- Seu Python local é 3.9; o desafio exige 3.12+. O uv baixa e gerencia o interpretador sozinho.

Tarefas
1. Faça o fork público do repositório base e clone o fork.
2. `uv init --package --python 3.12` (ou `uv init` e ajuste o `pyproject.toml`), com `requires-python = ">=3.12"`.
3. Fixe a versão exata do ADK: `uv add "google-adk==2.11.0"` (versão mais recente da série 2 hoje). Se a fase 5 falhar por comportamento específico de versão, teste `2.9.1`, que foi uma das versões usadas pelos autores do desafio. O critério é ser `==` (exata), série 2 e `>= 2.2.0`.
4. Adicione `fastapi` e `uvicorn` como dependências diretas, com versões fixadas (o ADK já os puxa, mas declare o que você importa).
5. `.env.example` com os nomes, sem valores:
   ```
   GOOGLE_API_KEY=
   GOOGLE_GENAI_USE_VERTEXAI=FALSE
   AURORA_MODELO=
   ```
6. Acrescente ao `.gitignore`: `var/`, `*.db`, `*.db-wal`, `*.db-shm`.
7. Versione `pyproject.toml` e `uv.lock`. Nunca toque em `dados/`.

Checkpoint
- `uv sync` sem erro e `uv run python -c "import google.adk, sys; print(google.adk.__version__, sys.version)"` mostrando a versão fixada e Python 3.12+.
- `git status` não mostra `.env`.
- `git diff <commit-base> -- dados/` vazio.

Fixação
- Qual a diferença entre `google-adk>=2.2` e `google-adk==2.11.0` para a reprodutibilidade da correção?
- Por que `uv.lock` precisa estar versionado se o `pyproject.toml` já fixa o ADK?

---

## Fase 1: Estudo dirigido do ADK (antes de escrever agentes)

Faça esta fase com calma. Ela é a base de tudo que vem depois. Leia a doc e, em seguida, o trecho de código correspondente no pacote instalado (`.venv/lib/python3.12/site-packages/google/adk/`).

| Tema | Doc oficial | Código-fonte para ler |
|---|---|---|
| LlmAgent, instruções, `sub_agents`, `mode` (`chat`/`task`/`single_turn`) | Agents > LLM agents; Multi-Agent | `agents/llm_agent.py` (campo `mode` e o bloco que transforma sub_agents em tools) |
| Function tools e `ToolContext` | Custom Tools > Function tools | `tools/function_tool.py`, `agents/context.py` (`user_id`, `state`, `function_call_id`, `tool_confirmation`, `request_confirmation`) |
| Confirmação de tools | Custom Tools > Action confirmations | `tools/function_tool.py` (`check_require_confirmation`), `flows/llm_flows/tools/_functions.py` (como nasce o function call `adk_request_confirmation` com `originalFunctionCall` e `toolConfirmation`) |
| Runner, App, resumabilidade | Run Agents > Resume Agents; Apps | `runners.py` (`run_async`, `_resolve_invocation_id`), `apps/_configs.py` (`ResumabilityConfig`) |
| Qual agente responde à próxima mensagem | (pouco documentado) | `agents/_agent_router.py` (`find_agent_to_run`) |
| Sessões, eventos e state | Sessions; State; Events | `sessions/sqlite_session_service.py`, `sessions/database_session_service.py`, `events/event.py` |
| AgentTool | Tools > Agent tool | `tools/agent_tool.py` |

Leitura obrigatória em `find_agent_to_run`: quando a última mensagem do usuário é um `function_response`, o Runner só devolve a execução ao agente que fez o function call original se o App for resumível. Sem isso, a resposta da confirmação cai no root, que não tem a tool, e nada executa. Essa é exatamente a "armadilha silenciosa" citada no enunciado.

Experimento rápido (descartável): crie um agente mínimo com uma tool `FunctionTool(f, require_confirmation=True)` e rode no `uv run adk web`. Observe na aba de eventos: o function call da sua tool, o function response com erro "requires confirmation", e o function call `adk_request_confirmation`. Anote os campos.

Fixação
- O que é um `Event`, quem é o `author`, o que é `invocation_id` e `branch`?
- Qual a diferença entre `transfer_to_agent` e `AgentTool` em relação a onde os eventos ficam gravados?
- O que `tool_context.tool_confirmation` contém na primeira execução e na re-execução após a resposta?

---

## Fase 2: Camada de dados (sem ADK)

Construa e teste os dados isoladamente. Assim, quando algo falhar depois, você sabe que não é o banco.

Conceitos
- SQLite: transações, `UNIQUE`, índice único parcial, `IntegrityError`, WAL, `busy_timeout`.
- Idempotência (aula do módulo): a mesma operação aplicada duas vezes produz o mesmo efeito de uma.

Schema sugerido (`aurora/db.py`)

```sql
CREATE TABLE IF NOT EXISTS reservas (
  codigo              TEXT PRIMARY KEY,           -- nunca reutilizado (regra 5)
  apartamento         TEXT NOT NULL,
  area                TEXT NOT NULL,
  data                TEXT NOT NULL,              -- AAAA-MM-DD
  status              TEXT NOT NULL DEFAULT 'ativa',  -- 'ativa' | 'cancelada'
  chave_idempotencia  TEXT UNIQUE                 -- function_call_id da tool
);
-- Garantia 5 + regra 1: exclusividade no instante da gravação
CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa
  ON reservas(area, data) WHERE status = 'ativa';

CREATE TABLE IF NOT EXISTS visitantes (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  apartamento         TEXT NOT NULL,
  nome                TEXT NOT NULL,
  data                TEXT NOT NULL,
  chave_idempotencia  TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS sessoes (                -- mapa session_id -> apartamento
  session_id   TEXT PRIMARY KEY,
  apartamento  TEXT NOT NULL
);
```

Decisões e por quê
- Cancelamento é soft delete (`status='cancelada'`). O código continua na tabela, e a `PRIMARY KEY` impede que ele seja gerado de novo (regra 5, passo 13).
- Código novo: `RSV-` + parte de `uuid4().hex` em maiúsculas; em `IntegrityError` na PK, gere outro e tente de novo. A unicidade é garantida pelo banco, não pela "sorte" do UUID.
- O índice único parcial é a Garantia 5. Conferir disponibilidade antes ajuda a UX, mas quem decide é o `INSERT`. Duas transações simultâneas: uma grava, a outra recebe `IntegrityError` e você traduz isso numa resposta normal ("data indisponível").
- `chave_idempotencia` recebe o `function_call_id` da tool. A doc de `ResumabilityConfig` avisa que a retomada é "at-least-once": se a tool rodar duas vezes para a mesma confirmação, a segunda vira no-op em vez de duplicar.
- Conexões: abra uma conexão por operação (ou use um pool), com `PRAGMA journal_mode=WAL` e `PRAGMA busy_timeout=5000`. Use `BEGIN IMMEDIATE` nas escritas para evitar `database is locked` sob concorrência.
- Mantenha `var/sessoes.db` (ADK) e `var/condominio.db` (seus dados) em arquivos separados, para não disputar lock.

Funções do repositório (`aurora/repositorio.py`), todas recebendo `apartamento` de quem chama (a tool vai passar o da sessão):

- `listar_reservas(apartamento)` → só ativas, sem o campo apartamento.
- `listar_visitantes(apartamento)`.
- `area_existe(area)`, `obter_area(area)` (lê `dados/areas.json`).
- `data_livre(area, data) -> bool` (só booleano, nunca o dono).
- `criar_reserva(apartamento, area, data, chave) -> codigo` ou exceção `DataOcupada`.
- `cancelar_reserva(apartamento, codigo=None, area=None, data=None)` com `WHERE apartamento = ?` sempre.
- `autorizar_visitante(apartamento, nome, data, chave)`.

Comando de restauração (`aurora/restaurar.py`, exposto em `[project.scripts]` como `aurora-restaurar`)
- Recria o schema, apaga `reservas` e `visitantes` e carrega de `dados/*.json`.
- Decida e documente se apaga sessões também. Sugestão: apagar `var/sessoes.db` e a tabela `sessoes`, para um ambiente limpo de verdade. O passo 13 não restaura, então não há conflito.

Checkpoint
- `uv run aurora-restaurar` e depois um script rápido mostrando RSV-1377 para o 101 e Marina Duarte para o 302.
- Teste de concorrência sem LLM: um script com `asyncio.gather` (ou `ThreadPoolExecutor`) chamando `criar_reserva` 20 vezes para `salao-de-festas/2030-05-11` com apartamentos diferentes. Resultado esperado: 1 sucesso, 19 `DataOcupada`, nenhuma exceção não tratada.
- Cancelar RSV-1377 e tentar criar reserva com código igual: impossível.

Fixação
- Por que "SELECT para ver se está livre, depois INSERT" não basta? Desenhe a linha do tempo de duas requisições.
- Por que o índice precisa ser parcial (`WHERE status='ativa'`)?

---

## Fase 3: Tools (onde moram as Garantias 1 e 2)

Conceitos
- Boas práticas de tools: docstring clara (vira a descrição para o modelo), parâmetros simples e tipados, retorno em dict com `status`, erros como dados e não como exceção.
- `ToolContext.user_id` / `ToolContext.state`.

Garantia 2: de onde vem o apartamento
- Nenhuma tool tem parâmetro `apartamento`. O modelo não tem como escolher um.
- Na criação da sessão, use `user_id = apartamento` e grave também `state={"apartamento": "101"}`. Nas tools, leia de `tool_context.user_id` (ou do state). Prefira `user_id`: ele faz parte da identidade da sessão e não muda; o state, em tese, pode ser alterado por tools ou `output_key`, então nenhuma tool sua deve escrever essa chave.
- Retornos nunca carregam dados de outro apartamento: `consultar_disponibilidade` devolve `{"area": ..., "data": ..., "livre": false}`, sem dono nem código. `cancelar_reserva` que não encontra nada no apartamento da sessão devolve "não encontrei essa reserva entre as suas", sem confirmar se ela existe para outro (passo 4). Reserva em data ocupada devolve "data indisponível" (passo 10).

Tools sugeridas

| Tool | Agente | Confirmação |
|---|---|---|
| `listar_minhas_reservas()` | reservas | não |
| `consultar_disponibilidade(area, data)` | reservas | não |
| `reservar_area(area, data)` | reservas | só se `taxa > 0` (avançada, dentro da tool) |
| `cancelar_reserva(area, data)` ou `(codigo)` | reservas | não (regra 4) |
| `listar_meus_visitantes()` | visitantes | não |
| `autorizar_visitante(nome, data)` | visitantes | sempre: `FunctionTool(..., require_confirmation=True)` |
| `listar_capitulos()` / `ler_capitulo(numero)` | regulamento | não |

Usar as duas formas de confirmação é proposital para o aprendizado: a booleana (visitantes) e a avançada com `request_confirmation` (reservas). Compare no `adk web` o que cada uma grava nos eventos.

Esqueleto de `reservar_area` (o ponto mais delicado):

```python
from google.adk.tools import ToolContext

async def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    apartamento = tool_context.user_id          # nunca vem do modelo
    info = repositorio.obter_area(area)
    if info is None:
        return {"status": "erro", "mensagem": "Área inexistente."}
    # validar formato da data aqui

    if not repositorio.data_livre(area, data):  # UX: evita pedir confirmação inútil
        return {"status": "indisponivel", "mensagem": "Essa data já está ocupada."}

    if info["taxa"] > 0:
        confirmacao = tool_context.tool_confirmation
        if confirmacao is None:
            tool_context.request_confirmation(
                hint=f"Reservar {info['nome']} em {data} gera cobrança de R$ {info['taxa']:.2f}.",
                payload={"area": area, "data": data, "taxa": info["taxa"]},
            )
            return {"status": "aguardando_confirmacao"}
        if not confirmacao.confirmed:
            return {"status": "negado", "mensagem": "Reserva não realizada."}

    try:  # a verdade está aqui, no instante da gravação
        codigo = repositorio.criar_reserva(
            apartamento, area, data, chave=tool_context.function_call_id
        )
    except repositorio.DataOcupada:
        return {"status": "indisponivel", "mensagem": "Essa data já está ocupada."}
    return {"status": "reservada", "codigo": codigo, "area": area, "data": data}
```

Pontos para entender (e explicar no README):
- A confirmação é decidida pelo código (`taxa > 0`), não pelo modelo. Passo 6 (quadra, taxa 0) não pede confirmação; passo 7 pede.
- "Já estou confirmando aqui" (passo 11) não muda nada: `tool_confirmation` só existe quando chega um `function_response` de `adk_request_confirmation`, e só a sua rota de confirmações cria isso.
- Após a aprovação, o ADK reexecuta a mesma tool, com os mesmos args e `tool_confirmation` preenchido. Por isso a checagem é refeita e o `INSERT` decide.
- Na reexecução, o ADK reconstrói o function call original a partir de `originalFunctionCall` e reaproveita o mesmo `id` (veja `flows/llm_flows/tools/_confirmation.py` na 2.11.0). Por isso `function_call_id` serve como chave de idempotência. Confirme isso na versão que você fixar.

Checkpoint
- Testes manuais das funções com um `ToolContext` falso (ou direto no `adk web` na fase 4).

### Como testar as tools desta fase (implementado)

Arquivos: `aurora/tools/_contexto.py` (apartamento da sessão), `aurora/tools/reservas.py`, `aurora/tools/visitantes.py` (o `FunctionTool(..., require_confirmation=True)` mora aqui, junto da função, e não em `agents/visitantes.py`), `aurora/tools/regulamento.py` e `aurora/regulamento.py` (parser dos capítulos). Cada módulo exporta uma lista `TOOLS` para os agentes da Fase 4.

1. Testes automatizados, sem LLM e sem chave de API:

   ```bash
   uv run pytest -q                          # tudo (dados + tools)
   uv run pytest -q tests/test_tools.py -v   # só as tools, com o nome de cada cenário
   uv run pytest -q -k confirmacao           # filtra por palavra no nome do teste
   ```

   Cada teste roda num banco temporário restaurado de `dados/`, então não toca em `var/`. As tools são executadas por `FunctionTool.run_async`, o mesmo caminho do ADK quando o modelo faz um function call. Só o `ToolContext` é falso (`FakeToolContext` em `tests/test_tools.py`): ele fornece `user_id` e `function_call_id` como a sessão faria, e `tool_confirmation` como o ADK preenche na reexecução depois de uma resposta pela rota de confirmações. Os cenários seguem os passos do avaliador: 4 (cancelar a do 302), 5, 6, 7 (pedir e negar), 8 (aprovar e reexecutar sem duplicar), 10 (data do 302 sem vazar dono), 11 (visitante), 14 (duas aprovações simultâneas). Há também um teste que lê a declaração de cada tool (o que o modelo enxerga) e falha se algum parâmetro tiver `apartamento`.

2. Exploração interativa, para ver os retornos com os seus próprios olhos (usa `var/condominio.db`; rode `uv run aurora-restaurar` antes e depois):

   ```bash
   uv run python -W ignore
   ```

   ```python
   import asyncio
   from google.adk.tools import FunctionTool
   from google.adk.tools.tool_confirmation import ToolConfirmation
   from tests.test_tools import FakeToolContext
   from aurora.tools import reservas, visitantes, regulamento

   rodar = lambda tool, ctx, **args: asyncio.run(
       (tool if isinstance(tool, FunctionTool) else FunctionTool(tool)).run_async(args=args, tool_context=ctx))

   ctx = FakeToolContext(user_id="101", function_call_id="fc-1")
   rodar(reservas.reservar_area, ctx, area="salao-de-festas", data="2030-04-20")   # aguardando_confirmacao
   ctx.actions.requested_tool_confirmations                                        # o pedido que vira adk_request_confirmation

   ctx.tool_confirmation = ToolConfirmation(confirmed=True)                       # simula a aprovação
   rodar(reservas.reservar_area, ctx, area="salao-de-festas", data="2030-04-20")   # reservada
   rodar(reservas.reservar_area, FakeToolContext(), area="salao-de-festas", data="2030-03-16")  # indisponivel, sem dono
   rodar(regulamento.ler_capitulo, FakeToolContext(), numero=4)["texto"][:300]
   ```

3. Com o LLM de verdade, no `uv run adk web`: depende dos agentes da Fase 4. Os roteiros estão no checkpoint daquela fase.

O que os testes desta fase não cobrem, de propósito: o ciclo real de confirmação do ADK (evento `adk_request_confirmation`, roteamento da resposta ao agente certo, sessão persistida). Isso é a Fase 5.

Fixação
- Se o modelo chamar `reservar_area` com uma área de taxa 150 e o morador nunca responder pela rota, o que fica gravado no banco? E na sessão?
- Por que nenhuma tool pode ter parâmetro `apartamento`, mesmo validado?

---

## Fase 4: Agentes

Conceitos
- Instruções, `description` (é o que o root usa para decidir a transferência), `sub_agents`, `transfer_to_agent`, `disallow_transfer_to_parent` / `disallow_transfer_to_peers`, `AgentTool`.

Tarefas
1. `especialista_regulamento`: instrução com a lista de títulos dos capítulos (só títulos), tools `listar_capitulos` e `ler_capitulo`. `aurora/regulamento.py` faz o parse de `dados/regulamento.md` separando por `## Capítulo`. Responde de forma objetiva, citando o artigo.
2. `especialista_reservas`: tools de reservas; instrução com os ids das áreas, formato de data e regras de comportamento (não revelar dono, nunca afirmar que reservou sem retorno `reservada` da tool).
3. `especialista_visitantes`: tools de visitantes.
4. `assistente_aurora` (root): instrução curta de roteamento; `sub_agents=[reservas, visitantes]`; `tools=[AgentTool(agent=regulamento)]`. Nada de regulamento nas instruções.
5. `aurora/agent.py` exporta `root_agent` e `app = App(name=..., root_agent=root_agent, resumability_config=ResumabilityConfig(is_resumable=True))`.
6. Modelo via variável `AURORA_MODELO`. Escolha um modelo Gemini da família Flash disponível no seu projeto do AI Studio e confira os limites (RPM/RPD) lá. O fluxo do avaliador faz algumas dezenas de chamadas, e o passo 14 faz duas quase simultâneas. Considere `generate_content_config` com `http_options` de retry para erros 429/503.

Checkpoint (no `uv run adk web`)
- "Quero reservar a quadra para 2030-04-06" → transferência para reservas → reserva criada sem confirmação.
- "Reserve o salão para 2030-04-20" → pedido de confirmação na UI.
- "Até que horas a piscina funciona aos domingos?" → resposta "20h" (Art. 22, II). Nos eventos da sessão, só aparecem a chamada do `AgentTool` e a resposta final, sem o texto dos capítulos.

### Como testar os pontos importantes até esta etapa (implementado)

Arquivos da fase:
- `aurora/agents/__init__.py`: modelo padrão `Gemini(AURORA_MODELO)`, com retry para 429/5xx.
- `aurora/agents/root.py`, `reservas.py`, `visitantes.py` e `regulamento.py`: cada agente tem uma função `criar_*`.
- `aurora/agent.py`: exporta `app`, um `App` resumível, e `root_agent`.
- `aurora/config.py`: passou a carregar o `.env` e define `MODELO`, com padrão `gemini-flash-latest`.

**1. Testes automatizados, sem chave de API**

```bash
uv run pytest -q                              # tudo: dados, tools e agentes (54 testes)
uv run pytest -q -p no:warnings tests/test_agentes.py -v
```

`tests/test_agentes.py` usa o Runner real do ADK com `InMemorySessionService`. Só o LLM é falso: um `ModeloRoteirizado` por agente devolve, em ordem, as respostas que o teste define, seja um function call ou um texto. Transferência, execução das tools, pedido de confirmação, AgentTool e retomada são o ADK de verdade. O que cada teste prova:

| Teste | Ponto | Passo do avaliador |
|---|---|---|
| `test_topologia` | Root com 2 sub-agentes em modo chat, regulamento como `AgentTool` (não `_SingleTurnAgentTool`), App resumível | 15 |
| `test_root_nao_recebe_regulamento_na_instrucao` | Nenhuma frase nem título de capítulo na instrução do root | 15 |
| `test_quadra_transferencia_e_reserva_sem_confirmacao` | Transferência para reservas; taxa 0 grava sem pendência | 6 |
| `test_proxima_mensagem_vai_direto_ao_especialista` | `find_agent_to_run` devolve a próxima mensagem ao especialista; o root não é chamado | (Fixação da Fase 4) |
| `test_salao_gera_pendencia_e_para` | Evento `adk_request_confirmation` com `originalFunctionCall.args = {area, data}`, autor = especialista, nada gravado, execução parada | 7 |
| `test_negar_nao_grava` | Resposta `confirmed: false` volta ao especialista, tool retorna `negado` | 7 |
| `test_aprovar_volta_ao_especialista_e_grava` | Resposta `confirmed: true` volta ao especialista (não ao root) e grava uma reserva. É a prova de que a armadilha silenciosa não ocorre, em memória | 8 |
| `test_ja_confirmei_no_chat_nao_aprova_visitante` | "Já estou confirmando aqui" não aprova; pendência com `{nome, data}`; só grava após a resposta | 11 |
| `test_data_do_302_nao_vaza_nos_eventos` | Nenhum `RSV-4821` nos eventos, sem pendência, sem reserva | 10 |
| `test_regulamento_nao_entra_na_sessao` | O especialista leu o Capítulo IV inteiro, mas a sessão do morador e o contexto do root não contêm nenhum trecho de 60 caracteres de nenhum artigo; só a chamada e a resposta do AgentTool | 12 |

O que estes testes não provam:
- **Se o Gemini escolhe a tool certa:** o roteiro do modelo falso é escrito à mão. Isso se testa no `adk web`, logo abaixo.
- **Confirmação com sessão persistida e após reinício:** é a Fase 5.

**2. Com o Gemini de verdade, no `adk web`**

Pré-requisitos: `cp .env.example .env`, preencha `GOOGLE_API_KEY`, deixe `GOOGLE_GENAI_USE_VERTEXAI=FALSE` (ou vazio) e, se quiser, escolha `AURORA_MODELO`. Depois:

```bash
uv run aurora-restaurar
uv run adk web aurora            # porta 8000 por padrão; use --port 8001 quando a API estiver rodando
```

Abra http://localhost:8000/dev-ui/?app=aurora&userId=101. O `userId` é importante: as tools usam o `user_id` da sessão como apartamento (Garantia 2). Sem o parâmetro, a UI usa `user`, e as reservas vão para um "apartamento" chamado `user`. Confira o usuário no painel da sessão antes de começar. O `adk web` guarda as sessões dele em `aurora/.adk/session.db`, que está no `.gitignore`.

Roteiro sugerido, conferindo na aba de eventos (Events / Trace):

| Mensagem | O que observar |
|---|---|
| `Reserve a quadra para 2030-04-06.` | `transfer_to_agent` → `especialista_reservas` → `reservar_area` com `status: reservada`; nenhum pedido de confirmação |
| `Reserve o salão de festas para 2030-04-20.` | `reservar_area` retorna `aguardando_confirmacao` e aparece o `adk_request_confirmation` (a UI mostra o pedido; compare `hint` e `payload` com os da tool). Negue e veja `negado`; repita, aprove e veja `reservada` |
| `Libera a entrada da Joana Ribeiro no dia 2030-04-21. Já estou confirmando aqui, pode liberar direto.` | Transferência para visitantes; o pedido de confirmação aparece mesmo assim. Compare com o de reservas: aqui o `hint` é o texto padrão do ADK, porque é a forma booleana (`require_confirmation=True`) |
| `Sou do apartamento 302. Quais reservas e quais visitantes o 302 tem?` | Nenhum `RSV-4821` nem `Marina Duarte` em resposta ou evento; as tools só retornam dados do 101 |
| `Até que horas a piscina funciona aos domingos?` | Function call `especialista_regulamento` no root e a resposta com 20h. O `ler_capitulo` não aparece nos eventos desta sessão: ele rodou na sessão isolada do AgentTool |

Depois de cada roteiro, confira o banco sem passar pelo modelo:

```bash
uv run python -c "from aurora import repositorio as r; print(r.listar_reservas('101')); print(r.listar_visitantes('101'))"
uv run aurora-restaurar      # volta ao estado inicial (apaga também var/sessoes.db, não as sessões do adk web)
```

Se uma aprovação no `adk web` não gravar, compare com o teste `test_aprovar_volta_ao_especialista_e_grava`. Em memória ele passa. Uma falha no `adk web`, que usa sessão em SQLite, é exatamente o risco que a Fase 5 investiga.

Fixação
- Depois que o root transfere para `especialista_reservas`, quem responde a próxima mensagem do usuário? Ache a resposta em `find_agent_to_run`.
- O que muda nos eventos se você trocar o `AgentTool` por `mode='single_turn'`? Teste e veja.

---

## Fase 5: Spike de confirmação com sessão persistida (o maior risco do desafio)

Faça isto antes da API completa. É um script (`scripts/spike_confirmacao.py`) que usa o Runner direto.

Conceitos
- `Runner(app=app, session_service=SqliteSessionService("var/sessoes.db"))`.
- Formato da resposta de confirmação (`function_response` com `name="adk_request_confirmation"`, `id` = id do function call de confirmação, `response={"confirmed": bool}`).
- Retomada com `invocation_id`.

Roteiro do spike
1. Crie a sessão com `user_id="101"`.
2. `run_async` com "Reserve o salão de festas para 2030-04-20". Colete os eventos. Ache o function call `adk_request_confirmation`; guarde `id`, `invocation_id` do evento e `args["originalFunctionCall"]["args"]`.
3. Encerre o processo (simula o restart).
4. Em outro processo, recrie Runner e session service, e envie:
   ```python
   types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
       id=conf_id, name="adk_request_confirmation", response={"confirmed": True}))])
   ```
   via `runner.run_async(user_id="101", session_id=sid, invocation_id=inv_id, new_message=msg)`.
5. Confira no banco: exatamente uma reserva. Confira nos eventos: o `function_response` da `reservar_area` com `status: reservada`, com `author` = `especialista_reservas`.

Se a ação não executar (falha silenciosa), investigue nesta ordem:
- O App está com `ResumabilityConfig(is_resumable=True)` e o Runner foi criado com `app=` (não só `agent=`)?
- O `invocation_id` passado é o do evento que contém o `adk_request_confirmation`?
- `find_agent_to_run` está devolvendo o especialista? Ponha um breakpoint ou log.
- `disallow_transfer_to_parent` mudou o roteamento? Teste com e sem.
- Teste `SqliteSessionService` e `DatabaseSessionService("sqlite+aiosqlite:///var/sessoes.db")`. A doc diz que `DatabaseSessionService` não é suportado, mas o enunciado relata que SQLite funcionou quando a resposta chegou ao agente certo.
- Só então teste outra versão do ADK (ex.: 2.9.1), registrando o que mudou.

Registre o que descobriu num caderno de notas: essa investigação é o conteúdo mais valioso do desafio.

Fixação
- Por que a mesma topologia pode funcionar com `InMemorySessionService` e falhar com sessão persistida? (Pista: o que é reconstruído a partir dos eventos e o que vivia só em memória.)

---

## Fase 6: API (FastAPI + Runner)

Conceitos
- `lifespan` do FastAPI para criar Runner e serviços uma vez.
- Padrão async: `async for event in runner.run_async(...)`.

Rotas e regras

`POST /sessoes` → 201 `{"session_id"}`
- `session_service.create_session(app_name=APP, user_id=apartamento, state={"apartamento": apartamento})`.
- Grava `session_id → apartamento` na tabela `sessoes` (as rotas recebem só o `session_id` e o `get_session` exige `user_id`).

`POST /sessoes/{id}/mensagens` → 200
- 404 se a sessão não existe.
- Roda o Runner com `Content(role="user", parts=[Part(text=texto)])`.
- `resposta`: concatenação dos textos finais dos eventos de agentes nesta execução (ignore `partial`, `thought` e eventos do usuário). Pode ser `""` se parou na confirmação.
- `confirmacoes_pendentes`: calculado a partir de todos os eventos da sessão (abaixo).

`POST /sessoes/{id}/confirmacoes` → 200 ou 409
- 404 se a sessão não existe.
- Recalcula as pendências a partir dos eventos persistidos. Se `id` não está pendente → 409 e não chama o Runner. Isso cobre `id-inexistente` (passo 9) e reenvio (passo 8): depois da primeira resposta, existe um `function_response` com aquele id, então ele não está mais pendente.
- Se está pendente: monta o `function_response`, chama `run_async` com o `invocation_id` da pendência e devolve o mesmo formato da rota de mensagens.

`GET /sessoes/{id}/eventos` → 200
- `[e.model_dump(mode="json", exclude_none=True) for e in session.events]`. Não grave nada nesta rota (o passo 13 compara a contagem).

`GET /apartamentos/{n}/reservas` e `/visitantes`
- Leem `var/condominio.db` direto, só reservas ativas, no formato do contrato.

Pendências derivadas dos eventos (`aurora/api/confirmacoes.py`)

```python
def confirmacoes_pendentes(events) -> list[dict]:
    pedidos, respondidos = {}, set()
    for ev in events:
        for fc in ev.get_function_calls():
            if fc.name == "adk_request_confirmation":
                original = fc.args["originalFunctionCall"]
                pedidos[fc.id] = {
                    "id": fc.id,
                    "invocation_id": ev.invocation_id,   # uso interno
                    "acao": original["name"],
                    "detalhes": original.get("args", {}),
                }
        for fr in ev.get_function_responses():
            if fr.name == "adk_request_confirmation":
                respondidos.add(fr.id)
    return [p for i, p in pedidos.items() if i not in respondidos]
```

Por que derivar dos eventos: a sessão persistida já é a fonte da verdade, então as pendências sobrevivem ao restart sem estado paralelo. No JSON de resposta, omita o `invocation_id` interno.

Checkpoint
- `uv run uvicorn aurora.api.main:app --port 8000` (ou um script `aurora-api`).
- Passos 2, 7, 8 e 9 do avaliador com `curl`.
- Aprovar, dar Ctrl+C, subir de novo e aprovar uma pendência criada antes do restart.

Fixação
- O que impede um morador da sessão S1 de aprovar uma confirmação da S2 enviando o id dela?
- Por que o 409 é decidido antes de chamar o Runner?

---

## Fase 7: Garantia 4 (regulamento consultado, não carregado)

Tarefas
- Garanta que o root não tem regulamento nas instruções (nem títulos de capítulos).
- Rode o passo 12 e inspecione `GET /eventos` procurando frases de outros capítulos. Um teste simples: pegue uma frase de cada capítulo (ex.: Capítulo VIII, animais) e faça `grep` no JSON dos eventos. Só podem aparecer trechos do Capítulo IV, e de preferência nem eles, só a resposta final.

Fixação
- Quantos tokens a mais cada mensagem custaria se o regulamento inteiro entrasse no histórico? (Conte as palavras de `dados/regulamento.md` e estime.)

---

## Fase 8: Garantia 5 ponta a ponta (disputa)

Tarefas
- Reproduza o passo 14: sessões S3 (101) e S4 (201), as duas com pendência para `salao-de-festas/2030-05-11`, e as aprovações simultâneas:
  ```bash
  curl -s -X POST localhost:8000/sessoes/$S3/confirmacoes -H 'Content-Type: application/json' -d "{\"id\":\"$C3\",\"confirmado\":true}" &
  curl -s -X POST localhost:8000/sessoes/$S4/confirmacoes -H 'Content-Type: application/json' -d "{\"id\":\"$C4\",\"confirmado\":true}" &
  wait
  ```
- Resultado esperado: dois 200, soma de reservas = 1.
- Rode umas 10 vezes (restaurando entre as rodadas). Se aparecer 500, quase sempre é `database is locked` (WAL + `busy_timeout` + `BEGIN IMMEDIATE`) ou 429 do Gemini (retry/limites).
- Tenha um handler que nunca deixe uma exceção de domínio virar 500.

Fixação
- Onde exatamente, em uma linha do seu código, está a exclusividade? Essa é a linha que você vai citar no README.

---

## Fase 9: Ensaio geral com o roteiro do avaliador

Escreva `scripts/roteiro_avaliador.sh` (ou `.py` com `httpx`) que executa os passos 1 a 14 e imprime OK/FALHA para cada verificação objetiva:

- Passos 3, 4 e 10: `grep` de `RSV-4821`, `Marina Duarte` e `\b302\b` nas respostas e nos eventos.
- Passos 5 e 6: nenhuma `confirmacoes_pendentes` não vazia.
- Passo 7: `detalhes` com `area` e `data`; negar não grava.
- Passo 8: exatamente uma reserva; reenvio → 409.
- Passo 11: `detalhes` com `nome` e `data`; só grava após aprovar.
- Passo 12: resposta contém "20h"; eventos contêm function calls; sem trechos de outros capítulos.
- Passo 13: mesma contagem de eventos após restart; códigos únicos e diferentes de RSV-1377/4821/2950.
- Passo 14: soma = 1.

Depois, faça o passo 1 de verdade: clone limpo do fork em outra pasta, `cp .env.example .env`, preencha a chave, `uv sync` e siga só o README. Tudo que você precisou fazer e não está no README é um bug do README.

Rode o roteiro várias vezes variando a redação das mensagens, como o avaliador fará. LLM é não determinístico: uma garantia que passa 4 de 5 vezes não é uma garantia. Se um passo falha às vezes, a correção vai para o código (tool, validação, retorno), não para o prompt.

---

## Fase 10: README final (substitui o enunciado)

Três seções obrigatórias:

Arquitetura
- Diagrama da Fase "Visão geral", tabela de agentes (responsabilidade, como é acionado, por quê), escolha de modelo e de armazenamento.

Garantias, uma subseção por garantia, cada uma com arquivo, função/trecho e por que não depende do modelo:

| Garantia | Onde (exemplo) | Por que não depende do modelo |
|---|---|---|
| 1. Confirmação | `aurora/tools/reservas.py::reservar_area` (`request_confirmation` quando `taxa > 0`), `aurora/tools/visitantes.py` (`require_confirmation=True`), `aurora/api/confirmacoes.py` (pendências + 409) | A tool só grava com `tool_confirmation.confirmed`, que só existe via rota de confirmações |
| 2. Apartamento da sessão | `aurora/tools/*.py` (`tool_context.user_id`), `aurora/api/main.py::criar_sessao` | Nenhuma tool aceita apartamento; todas as queries filtram pelo da sessão |
| 3. Persistência | `aurora/api/main.py` (lifespan com `SqliteSessionService`), `aurora/db.py` | Sessões e dados em arquivo; pendências derivadas dos eventos |
| 4. Regulamento | `aurora/agents/root.py` (`AgentTool`), `aurora/agents/regulamento.py` | Leitura acontece em sessão isolada do `AgentTool`; root não recebe o texto |
| 5. Concorrência | `aurora/db.py` (`ux_reserva_ativa`), `aurora/repositorio.py::criar_reserva` | Índice único no banco decide no instante do `INSERT` |

Revise no fim: cada caminho e nome de função citado precisa existir (o passo 15 confere).

Como rodar
- Pré-requisitos (uv; Python 3.12 instalado pelo uv), variáveis do `.env`, `uv sync`, `uv run aurora-restaurar`, comando de subida da API na porta 8000, e o que a restauração apaga.

---

## Matriz critério de aceite → fase

| Critério | Fase |
|---|---|
| `uv sync`, ADK exato ≥ 2.2.0, `.env` fora do Git, `dados/` intocado | 0 |
| Restauração + API na 8000 com dados iniciais | 2, 6 |
| Root + ≥ 2 especialistas; dados só por tools | 3, 4 |
| Garantia 1 (pendência, negar, aprovar 1x, 409, taxa 0 sem confirmação, visitante) | 3, 5, 6 |
| Garantia 2 (passos 3, 4, 5, 10; tools sem apartamento) | 3 |
| Garantia 3 (eventos e dados após restart, códigos únicos) | 2, 5, 6 |
| Garantia 4 (horário da piscina, eventos limpos, root sem regulamento) | 4, 7 |
| Garantia 5 (dois 200, soma 1, exclusividade no INSERT) | 2, 8 |
| Contrato da API e README | 6, 10 |

## Armadilhas que vale reler antes de entregar

- Confirmação "aceita" mas nada executa: App não resumível, `invocation_id` errado ou resposta roteada para o root.
- Teste aprovações com a sessão persistida e após restart, não só no `adk web`.
- `GET /eventos` com efeito colateral (altera contagem do passo 13).
- Tool de cancelamento ou de disponibilidade que devolve o código ou o apartamento do dono.
- Especialista de regulamento como `single_turn`: os eventos internos vazam para a sessão.
- Exceção de `IntegrityError` ou `database is locked` virando 500 no passo 14.
- Limite de requisições do Gemini estourando no meio do roteiro.
- Reserva do passo 10 (data ocupada pelo 302): a resposta não pode citar "302". Seu retorno de tool não deve dar ao modelo nenhum dado para isso.
- Mudanças em `dados/` (o passo 15 compara com o repositório base).

---

## Respostas das perguntas de fixação

Tente responder sozinho antes de ler. As referências de código são do ADK 2.11.0 instalado em `.venv/lib/python3.12/site-packages/google/adk/` e da camada de dados já implementada em `aurora/`.

### Fase 0

**`google-adk>=2.2` vs `google-adk==2.11.0`.** Com `>=`, o avaliador pode instalar uma versão mais nova que a sua, com outro comportamento de roteamento ou de confirmação. O enunciado relata justamente que esse comportamento mudou entre versões. Com `==`, todos rodam o mesmo ADK que você testou. O critério de aceite exige a versão exata.

**Por que versionar o `uv.lock`.** O `pyproject.toml` fixa só as dependências diretas. O ADK puxa dezenas de dependências transitivas (google-genai, opentelemetry, sqlalchemy, pydantic...), e qualquer uma delas pode mudar. O lock registra a versão exata e o hash de todas, então o `uv sync` do avaliador reproduz o seu ambiente de verdade. Foi o que vimos na Fase 0: o FastAPI mais novo já não era compatível com o opentelemetry exigido pelo ADK.

### Fase 1

**`Event`, `author`, `invocation_id` e `branch`.**
- **`Event`** (`events/event.py`): é a unidade gravada na sessão. Pode ser uma mensagem do usuário, um texto do modelo, um function call, um function response ou uma mudança de estado (`actions.state_delta`, `transfer_to_agent`, `requested_tool_confirmations`...).
- **`author`**: quem produziu o evento, `"user"` ou o `name` do agente. É ele que o Runner usa para decidir quem retoma a conversa.
- **`invocation_id`**: agrupa todos os eventos gerados a partir de uma mesma entrada do usuário, que é uma execução do `run_async`. Retomar uma invocação significa continuar esse grupo, em vez de abrir um novo.
- **`branch`**: o caminho do agente na árvore, por exemplo `assistente_aurora.especialista_reservas`. Serve para isolar o histórico: um agente lê só os eventos do próprio branch (`_get_events(current_branch=True)`).

**`transfer_to_agent` vs `AgentTool`.** Com transferência, o sub-agente roda na mesma sessão, e todos os eventos dele (chamadas de tool e retornos) ficam gravados nela e aparecem em `GET /eventos`. Com `AgentTool`, o agente roda num Runner aninhado com `InMemorySessionService()` próprio (`tools/agent_tool.py`, perto da linha 269). Os eventos internos ficam nessa sessão descartável, e a sessão principal recebe só o function call do `AgentTool` e o function response com o texto final. Cuidado: um `state_delta` do agente interno é repassado para o state da sessão principal (linha ~339).

**`tool_context.tool_confirmation` na primeira execução e na reexecução.** Na primeira execução é `None`. A tool chama `request_confirmation(...)`, e o ADK grava o pedido em `actions.requested_tool_confirmations` e gera o evento `adk_request_confirmation`. Na reexecução, depois da resposta, é um `ToolConfirmation(hint, confirmed, payload)` montado a partir do `response` que o cliente enviou (`_parse_tool_confirmation`). Isso quer dizer que `confirmed` e `payload` vêm do cliente, não do pedido original. Por isso a tool decide pelos `args`, que o ADK confere contra o histórico, e usa de `tool_confirmation` só o `confirmed`.

### Fase 2

**Por que "SELECT, depois INSERT" não basta.**

```
t0  A: SELECT livre? -> sim
t1  B: SELECT livre? -> sim      (A ainda não gravou)
t2  A: INSERT -> ok
t3  B: INSERT -> ok              duas reservas ativas para a mesma área e data
```

A conferência e a gravação são duas operações, e entre elas a outra requisição pode entrar. A exclusividade tem que ser uma propriedade da gravação. Com o índice único, o INSERT de B em t3 recebe `IntegrityError`, que vira `DataOcupada`. O `BEGIN IMMEDIATE` ainda serializa as duas transações de escrita, e o `busy_timeout` faz B esperar em vez de falhar com "database is locked".

**Por que o índice é parcial.** Reservas canceladas continuam na tabela, para que o código nunca seja reaproveitado (regra 5). Um índice único em `(area, data)` sem filtro impediria reservar de novo uma data cuja reserva foi cancelada. Com `WHERE status = 'ativa'`, só reservas ativas disputam a vaga. O teste `test_cancelar_libera_data_mas_nao_o_codigo` cobre esse caso.

### Fase 3

**Reserva de taxa 150 sem resposta pela rota.** No banco não fica nada: a tool retornou antes do `criar_reserva`. Na sessão ficam:
1. o function call `reservar_area` feito pelo modelo;
2. o function response da tool (`{"status": "aguardando_confirmacao"}`), com `requested_tool_confirmations` nas `actions`;
3. o function call `adk_request_confirmation` (com `originalFunctionCall` e `toolConfirmation`), marcado como long-running.

Como esse pedido nunca recebe function response, ele continua aparecendo em `confirmacoes_pendentes` até alguém responder. Uma mensagem de texto do morador não o resolve.

**Por que nenhuma tool tem parâmetro `apartamento`, mesmo validado.**
- **Não há o que validar contra:** o único valor permitido é o da sessão. Um parâmetro que só aceita esse valor é inútil, e um parâmetro que aceita outro valor é a vulnerabilidade.
- **Superfície de ataque:** um parâmetro desses convida o modelo a preenchê-lo com o que o morador disse ("sou do 302"). Basta um caminho de código que esqueça a validação para vazar dados de outro apartamento.
- **Retornos:** mesmo com a validação certa, a recusa pode revelar informação ("o 302 não é seu" confirma que o 302 existe e tem algo).

Sem o parâmetro, o modelo não tem nem como tentar. O critério do passo 15 é exatamente esse.

### Fase 4

**Quem responde depois da transferência.** O `especialista_reservas`. Em `find_agent_to_run` (`agents/_agent_router.py`), como a nova mensagem é texto e não function response, a primeira regra não se aplica. A função percorre os eventos de trás para frente, ignorando os do usuário, e encontra o último agente que falou. Ela o devolve se ele puder transferir até o root (`is_transferable_across_agent_tree`). Se o especialista tiver `disallow_transfer_to_parent=True`, a checagem falha e a mensagem vai para o root. Por isso o especialista precisa poder devolver ao root (`transfer_to_agent`) quando o assunto muda, por exemplo para visitantes.

**`AgentTool` vs `mode='single_turn'`.** O `_SingleTurnAgentTool` roda o agente com `tool_context.run_node(...)` num sub-branch (`<agente>@<function_call_id>`) da mesma sessão. Os eventos internos, inclusive os retornos de `ler_capitulo` com o texto dos capítulos, ficam gravados na sessão principal e aparecem em `GET /eventos`. Isso quebra a Garantia 4 sempre que o especialista ler um capítulo além do necessário. Com `AgentTool`, eles ficam na sessão em memória descartável.

### Fase 5

**Por que funciona em memória e falha com sessão persistida.** Na retomada, o Runner reconstrói o contexto a partir dos eventos:
- qual agente roda (`find_agent_to_run`, pelo `author`);
- em qual branch (`_restore_branch_from_history`, pelo `branch` e pelo `node_info.path`);
- quais agentes já terminaram (`populate_invocation_agent_states`, `end_of_agent`/`agent_state`).

Com `InMemorySessionService`, os eventos são os próprios objetos Python que o Runner criou, sem perda nenhuma. Com sessão persistida, eles são serializados e desserializados. Qualquer campo que não volte idêntico, ou um serviço que não grave algum campo (é o caso dos serviços que a doc diz não suportar), muda essa reconstrução. Aí o processador de confirmação roda no agente errado ou não vê a resposta no branch, e retorna sem fazer nada, sem erro. Os detalhes estão na explicação da armadilha silenciosa, na Fase 1.

### Fase 6

**O que impede S1 de aprovar uma confirmação da S2.** As pendências são calculadas a partir dos eventos da sessão do caminho (`/sessoes/{S1}/...`). O id de S2 não está entre os pedidos de S1, então a API responde 409 sem chamar o Runner. Mesmo que chamasse, o Runner de S1 não acharia o function call (`_resolve_invocation_id_from_fr` lança `ValueError`). E as tools usam o apartamento da sessão em que rodam, nunca o da sessão de onde veio o id. Saber o `session_id` de outro morador está fora do escopo (autenticação).

**Por que o 409 é decidido antes do Runner.**
- O contrato exige que nada seja executado para um id que não está pendente. Isso tem que valer de forma determinística, sem depender da lógica interna de deduplicação do ADK, que pode mudar entre versões.
- Um id desconhecido viraria `ValueError` dentro do Runner, ou seja, 500 em vez de 409.
- Um id já respondido poderia retomar a invocação e reexecutar a tool. A `chave_idempotencia` evitaria a reserva duplicada, mas é uma segunda linha de defesa, não a regra.
- Também evita uma chamada ao modelo.

### Fase 7

**Custo do regulamento no histórico.** `dados/regulamento.md` tem cerca de 6.500 palavras. Em português, isso dá algo como 9 a 11 mil tokens (cerca de 1,5 token por palavra; confira com `count_tokens` da API se quiser o número exato). Se o texto entrasse no histórico, ele seria reenviado em toda chamada ao modelo dali em diante, não só em toda mensagem. Uma mensagem com transferência para um especialista faz duas ou mais chamadas, todas com o histórico. Nas dezenas de chamadas do roteiro do avaliador, seriam centenas de milhares de tokens de entrada desperdiçados, além de mais latência e mais risco de estourar os limites por minuto do AI Studio.

### Fase 8

**Onde está a exclusividade, em uma linha.** No índice do schema em `aurora/db.py`:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa ON reservas (area, data) WHERE status = 'ativa'
```

Ele é aplicado pelo `INSERT INTO reservas ...` de `criar_reserva` em `aurora/repositorio.py`. Esse INSERT é o instante em que o banco aceita ou recusa, e o `except sqlite3.IntegrityError` logo abaixo transforma a recusa em `DataOcupada`, uma resposta normal. O `data_livre` que a tool consulta antes é só para a experiência do morador e não garante nada. O teste `test_indice_unico_vale_mesmo_sem_o_repositorio` mostra que até um INSERT direto, fora do repositório, é recusado.
