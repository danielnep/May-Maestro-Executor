# Horizon | Equipe de 17 especialistas

Runtime Python 3.11+ com chamadas diretas às APIs Gemini e DeepSeek e SDK oficial Railway em Node.js 22+. Não instala Codex CLI, Gemini CLI ou Railway CLI e não exige VPS. O SDK `railway@3.16.0` é uma biblioteca, não uma CLI. Os 17 agentes têm configuração, instruções, ferramentas, contexto de chamada e memória próprios. Gemini é o provedor preferido; DeepSeek assume quando ele não estiver disponível por qualquer motivo. Compartilham código de infraestrutura e podem compartilhar uma chave; o Maestro delega tarefas, não escreve as conclusões dos especialistas.

## Estado da entrega

Implementados: 17 perfis, gateway REST, fila SQLite, agendamento, dependências, memória por agente/projeto, checkpoints com retomada, ferramentas de leitura/escrita, testes isolados em Docker, persistência GitHub por API e publicação em branch de trabalho. A suíte usa HTTP simulado para verificar o contrato sem consumir API.

**Provas reais concluídas:** 17 chamadas independentes ao DeepSeek, leitura real de arquivo, memória e checkpoint por especialista; criação e reuso de sandbox Railway; teste Python isolado em Docker dentro dessa VM; checkpoint GitHub recuperado em outro runner. Ver [Actions 39165714831](https://github.com/danielnep/May-Maestro-Executor/actions/runs/39165714831).

O fallback real usado nessa prova foi por configuração Gemini ausente (modelo/atestação), não por cota real esgotada. Falhas 401/429/5xx, transporte e respostas inválidas foram verificadas com simulação. A chamada individual prova conexão e execução de ferramenta; ainda não substitui uma entrega conjunta complexa dos 17. O fluxo periódico segue aguardando aprovação do workflow na principal e habilitação do worker. Os 10 novos perfis são agentes do runtime, não plugins privados já instalados no ChatGPT.

## Especialistas

| Agente | Responsabilidade | ID executável |
| --- | --- | --- |
| May Maestro | Coordenar prioridades, dependências e critérios de aceite; delegar trabalho com contratos verificáveis. | `may-maestro` |
| Horizon Project Studio | Converter o pedido em requisitos e critérios de aceite sem implementar engenharia de todas as áreas. | `horizon-project-studio` |
| Software Architect | Definir limites de componentes, contratos e decisões arquiteturais. | `software-architect` |
| Frontend Engineer | Implementar interface web acessível e responsiva preservando contratos. | `frontend-engineer` |
| Backend Engineer | Implementar APIs, autenticação e regras de negócio com integridade. | `backend-engineer` |
| Mobile App Builder | Desenvolver recursos Android nativos e integração com o dispositivo. | `mobile-app-builder` |
| UI/UX Designer | Projetar fluxos, hierarquia e interfaces para tarefas reais. | `ui-ux-designer` |
| Horizon UI Guardian | Auditar interface real, acessibilidade, consistência e fidelidade visual. | `horizon-ui-guardian` |
| Database Engineer | Modelar dados, consultas e migrações com integridade e desempenho. | `database-engineer` |
| Integration Engineer | Integrar APIs externas, webhooks, MCP e automações com contratos explícitos. | `integration-engineer` |
| QA Engineer | Verificar comportamento e regressões por testes executáveis. | `qa-engineer` |
| Security Engineer | Revisar limites de confiança, autorização e exposição de dados. | `security-engineer` |
| DevOps Engineer | Implementar CI, ambientes, observabilidade e entrega reversível. | `devops-engineer` |
| Horizon Evolution Agent | Diagnosticar e priorizar melhorias com prova antes/depois. | `horizon-evolution-agent` |
| Horizon Skills | Pesquisar e manter conhecimento técnico reutilizável com fontes e validade. | `horizon-skills` |
| Corregedoria May | Fiscalizar veracidade, conformidade e rastreabilidade das entregas. | `corregedoria-may` |
| May Persona | Revisar linguagem e diretrizes editoriais preservando precisão técnica. | `may-persona` |

`agents/<id>/agent.json` seleciona modelo/chave; `instructions.md` define critérios próprios; `tools.json` restringe capacidades. Não há geração automática de 17 pareceres numa chamada do Maestro. Cada ação de cada agente passa pelo seu próprio pedido `generateContent`. Skills privadas do ChatGPT não transferem automaticamente seu texto ou suas conexões para um processo externo; os contratos deste repositório são a fonte que o runtime realmente lê.

## Operação sem terminal do usuário

1. As tarefas ficam em `tasks/inbox.json` **na branch de trabalho**, editáveis pela interface do GitHub. `tasks/example.json` é um exemplo pronto de análise somente leitura. IDs são idempotentes; para alterar um pedido já importado, criar ID novo.
2. Em Actions, abrir **Horizon - 17 especialistas via API** (arquivo `agent-executors.yml`, workflow já existente no repositório) e selecionar a branch de trabalho no botão Run workflow.
3. O worker é habilitado por `HORIZON_WORKER_ENABLED=true`. Configure o secret `DEEPSEEK_API_KEY` para o fallback autorizado. Para Gemini configure também `GEMINI_API_KEY`, `GEMINI_MODEL` e `GEMINI_FREE_TIER_CONFIRMED=true`. A falta de configuração Gemini aciona o DeepSeek em vez de bloquear a equipe inteira. A branch pode ser escolhida em `HORIZON_WORK_BRANCH`; o padrão é `feat/horizon-team-runtime-20261018`.
4. A atestação de free tier deve corresponder a um projeto Google **sem billing associado**. O cliente não possui API que comprove isso e nunca habilita billing. O fallback para DeepSeek foi autorizado pelo proprietário em 18/10/2026 para todos os agentes, em qualquer indisponibilidade do Gemini, inclusive autenticação/configuração, cota, transporte ou resposta inválida. Uma assinatura Gemini/ChatGPT não configura este secret.
5. Não há teto artificial padrão de tokens, chamadas ou etapas para economizar. DeepSeek usa `thinking=enabled`, `reasoning_effort=max`, modelo oficial atual `deepseek-flash` (substituível por `DEEPSEEK_MODEL`). O histórico de decisões é preservado. Ao terminar uma fatia técnica de execução, o worker salva o progresso, cede a tarefa e retoma em outro lote, sem consumir tentativas de erro. A janela da sessão Actions é operacional, não uma cota de raciocínio.
6. Fila, memória e conteúdo de arquivos gerados são salvos em `agent-state/runtime`, via snapshot SQLite consistente. Checkpoint é confirmado remotamente depois de cada ação. O worker publica arquivos somente na branch autorizada, sem merge, sem deploy e sem force push. Se a branch mudou, para em conflito.

O workflow de verificação roda em push/PR sem API. A checagem de configuração em push informa apenas se chave/modelo/atestação existem, sem revelar valores. Chamadas reais não rodam em PRs. O agendamento horário (`17 * * * *`, UTC) só passa a existir depois da aprovação deste workflow na branch padrão. GitHub pode atrasar agendamentos; não há promessa de execução pontual ou processo 24/7. Sem tarefas não há chamada de IA. Não foi contratado nenhum recurso de hospedagem.

O runner padrão de um repositório público não cobra minutos de execução; não usamos runners maiores nem criamos serviços de nuvem. O workflow novo não cria artifacts de armazenamento: as evidências ficam nos logs e no estado versionado. A gratuidade Gemini depende do projeto Google, não deste código. A autorização atual permite usar a chave DeepSeek secundária sem racionamento econômico; o gateway não cria saldo nem altera a cobrança dos provedores.

## Contrato da tarefa

```json
[
  {
    "task_id": "revisao-publica-001",
    "agent": "may-maestro",
    "project": "demo-publica",
    "objective": "Ler README.md, delegar a análise dos requisitos e a revisão de evidências a especialistas distintos. Informar bloqueios reais.",
    "payload": {
      "branch": "feat/horizon-team-runtime-20261018",
      "allowed_paths": []
    },
    "priority": 10,
    "max_attempts": 3
  }
]
```

`not_before` aceita timestamp Unix para agendamento individual. Dependências `depends_on` precisam apontar a tarefas já importadas. O Maestro cria DAGs limitados a 17 etapas; revisores recebem resultados das dependências. Ferramentas e arquivos permitidos são herdados, nunca ampliados pelo plano. `allowed_paths=[]` implica somente leitura. Para edição, dar a lista de arquivos/padrões aprovada no pedido. Cada processo atualmente trabalha num checkout e numa branch; multi-repositório requer outro checkout/coordenação e token dedicado, não é herdado da conexão GitHub do ChatGPT.

## Ferramentas e limites atuais

- `list_files` e `read_file`: arquivos de projeto, com limites; sem `.git`, arquivos ocultos, symlinks, runtime, bancos e caminhos sensíveis.
- `write_file`: somente especialista com capacidade, branch Git real correspondente e allowlist da tarefa. Não altera seu próprio control plane, workflows, credenciais nem branch principal. Conflitos de conteúdo podem usar SHA-256. Escritas ficam no estado para reconstrução após perda do runner.
- `run_check`: inicialmente `python-unittest`. Copia arquivos permitidos para snapshot sanitizado e roda em Docker sem rede, credenciais, privilégios ou escrita no projeto; tempo, CPU, memória e saída limitados. Docker indisponível ou comando não suportado é bloqueio explícito. Não executa shell enviado pelo modelo no host.
- `remember`: memória isolada por projeto e agente. Dependências compartilham entregas por contrato, não acesso irrestrito a todas as memórias.
- `delegate`: exclusivo do Maestro; IDs determinísticos, sem recursão, sem ciclos, sem expandir escopo.
- `finish`: registra relatório. `status=succeeded` significa **etapa reportada**, não teste aprovado, código perfeito ou aprovação humana. O campo `verification` guarda retorno real das ferramentas, inclusive falhas.

Os especialistas Mobile, UI Guardian e DevOps podem analisar e implementar arquivos dentro do escopo; este primeiro motor ainda não fornece build Android/Gradle, navegação interativa ou screenshots com visão multimodal. Não anunciar APK, revisão visual ou deploy comprovados até integrar e testar esses adaptadores. Testes Python não substituem testes desses recursos.

## Fallback coletivo e qualidade

`ModelGateway` serve aos 17 agentes. Qualquer falha Gemini é registrada por provedor/motivo/status e redireciona a decisão ao DeepSeek, preservando a identidade, o objetivo e o estado. Um circuit breaker compartilhado evita repetir a chamada Gemini em cada especialista durante a indisponibilidade; o cooldown é salvo no estado e a próxima ação após a janela tenta Gemini novamente. O DeepSeek pode operar mesmo que Gemini ainda não tenha modelo ou atestação configurados. Se ambos falham, a tarefa conserva o checkpoint e volta à fila; falha de provedor não é teste aprovado. O gateway não repete ferramentas já executadas.

Não são enviados `maxOutputTokens`/`max_tokens` por padrão. Raciocínio DeepSeek fica habilitado com esforço máximo; o texto interno de raciocínio não é armazenado nem publicado. Timeouts de transporte, memória/processo e bloqueios de segurança continuam sendo limites técnicos. O histórico não é cortado nas últimas 12 ações, e a tarefa não falha ao chegar à 12ª etapa. `State.yield_task` e renovação de lease permitem continuar em novas sessões.

## Railway: criar ou reutilizar sandbox sem CLI

Secrets: `RAILWAY_API_TOKEN`; repository variable: `RAILWAY_ENVIRONMENT_ID`. O ambiente informado pelo proprietário é `6c3d13e1-d963-49c0-8121-8c75bd172766`, no projeto `caf3c2ea-f857-4242-8fa3-5453ea1462ca`. Os workflows já encaminham esses valores para o broker Python e o SDK oficial Node.

Quando um agente com permissão usa `run_check`, o broker recupera o ID da VM gerenciada do estado. O SDK conecta, verifica o marcador de propriedade e reutiliza. Se ela foi destruída por inatividade, procura outra VM gerenciada compatível; se não existe, cria uma nova com isolamento de rede e ambiente vazio. Nunca adota nem destrói uma VM aleatória/manual do usuário. O ID novo é salvo antes de testar.

Apenas arquivos sanitizados são copiados a uma pasta nova por operação. O teste roda em Docker dentro do sandbox, sem rede nem credenciais do coordenador. Chaves Gemini/DeepSeek/GitHub e token Railway não são injetados na VM. A pasta temporária e o container são limpos ao terminar. `exitCode`, timeout, truncamento, cleanup e número de testes executados são verificados; zero testes não conta como aprovação. O código do projeto não é executado no processo que contém as credenciais.

A VM segue o timeout de inatividade do plano; não há garantia de 72 horas, e o worker pode recriá-la. O checkpoint durável fica no GitHub, fora dela. O adaptador inicial executa `python-unittest`; o contêiner `python:3.12-slim` não contém Git, portanto suítes que precisam de Git ou outras dependências exigem uma imagem apropriada antes de serem aprovadas. Build Android, browser interativo e instalação de dependências de projetos ainda não estão incluídos nesse check.

## Segurança e durabilidade

Este repositório é público. O estado remoto contém objetivos, decisões, histórico e arquivos gerados: usar **somente projetos públicos e dados sintéticos**, nunca dados de clientes, dados pessoais ou segredos. Não existe classificação automática infalível de conteúdo. Para projetos privados, mover estado e execução para destino privado com controle de acesso antes de importar esses dados.

SQLite usa transações e leases para dois workers locais não pegarem o mesmo trabalho. Snapshots de instâncias SQLite em runners diferentes não são uma fila distribuída: o workflow usa um único grupo `concurrency`, e o transporte usa compare-and-swap. Não criar um segundo coordenador sobre a mesma branch de estado. Na VPS, manter a mesma máquina de estados e trocar o armazenamento por banco compartilhado antes de aumentar concorrência.

Falha de persistência interrompe o coordenador. A última ação sem confirmação remota pode precisar de reconciliação; as ferramentas de escrita/memória são repetíveis, e a publicação verifica a referência remota. Não há atomicidade mágica entre GitHub, disco e provedor de IA; uma chamada que perdeu resposta pode consumir cota mesmo sem resultado recuperado. O snapshot preserva progresso entre execuções; o agente não continua processando enquanto não houver worker ativo.

## Verificações reproduzíveis

Comandos usados pelo runner (não exigem instalação de CLI de agente no seu telefone):

```sh
python3 -m unittest discover -s tests -v
python3 -m horizon_core.app --list
python3 -m horizon_core.app --preflight
python3 -m horizon_core.app --tasks tasks/example.json --db runtime/state.sqlite3
```

A suíte verifica identidades e requisições independentes, leitura/memória por 17 perfis, ferramentas reais em fixtures Git, retomada SQLite após interrupção, DAG, dependências, falhas de QA preservadas, lease expirado, concorrência, orçamento, snapshot remoto simulado, proteção de caminhos e Docker simulado sem credenciais. O novo ciclo tem 98 testes Python e 17 testes Node com transportes simulados, além das provas externas indicadas no checkpoint. Os sete auditores estáticos em `maestro_exec/` e `executors/` permanecem para compatibilidade; o caminho de codificação via Codex CLI foi retirado. `supervisor.py` encaminha ao runtime ativo; não contém mais o retorno antecipado desativando Gemini.

## Evidências da execução remota

[Actions 39165714831](https://github.com/danielnep/May-Maestro-Executor/actions/runs/39165714831), código `01fcce127cd0894dab2837f8e99ba734366315a5`:

- `verification`: suíte Python e 17 testes Node aprovados.
- `providers-proof`: 17 perfis, 17 chamadas reais `deepseek-flash`, 17 leituras de arquivo, memória recuperada e checkpoints. Snapshot em `agent-state/providers-proof-39165714831`; leitura independente confirmou 17 IDs distintos e motivo primário `configuration`.
- `railway-proof`: VM `9de2a58a-27c9-42fc-966c-6b577a65ba00` criada e reutilizada; `python-unittest` passou com um teste real, sem timeout/truncamento e com limpeza confirmada. Estado em `agent-state/railway-proof-39165714831`.
- `smoke-prepare` e `smoke-resume`: checkpoint remoto e retomada em outro runner aprovados.
- `configuration`: Gemini key presente, modelo/atestação ausentes; DeepSeek key presente; Railway token e ambiente presentes. Nenhum valor secreto foi extraído.

A VM pode já ter expirado por inatividade quando este documento for lido. O broker recria uma VM gerenciada quando necessário; estes IDs são evidência histórica, não promessa de VM viva.

## Critério de ativação

O código e suas conexões têm evidência real. Para produção operacional da equipe, ainda falta aprovar o workflow na branch principal, habilitar o coordenador periódico e comprovar uma entrega conjunta completa com revisão independente. Na configuração atual o DeepSeek atende a equipe; Gemini volta a ser preferido quando seu modelo e projeto gratuito forem configurados. Não anunciar testes visuais, APK ou deploy sem os respectivos adaptadores e provas.

## Referências oficiais verificadas

- [SDK Railway](https://docs.railway.com/sandboxes)
- [DeepSeek thinking](https://api-docs.deepseek.com/guides/thinking_mode/)
- [DeepSeek API](https://api-docs.deepseek.com/api/create-chat-completion/)
- [Gemini generateContent](https://ai.google.dev/api/generate-content)
- [Billing Gemini](https://ai.google.dev/gemini-api/docs/billing)
- [Limites Gemini](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Runners GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)
- [Eventos/agendamentos Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
