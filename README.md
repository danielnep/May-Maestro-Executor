# Horizon | Equipe de 17 especialistas

Runtime Python 3.11+ com chamadas diretas à API Gemini. Não instala nem depende de Codex CLI, Gemini CLI, VPS ou servidor pago. Os 17 agentes têm configuração, instruções, ferramentas, contexto de chamada e memória próprios. Compartilham código de infraestrutura e podem compartilhar uma chave; o Maestro delega tarefas, não escreve as conclusões dos especialistas.

## Estado da entrega

Implementados: 17 perfis, gateway REST, fila SQLite, agendamento, dependências, memória por agente/projeto, checkpoints com retomada, ferramentas de leitura/escrita, testes isolados em Docker, persistência GitHub por API e publicação em branch de trabalho. A suíte usa HTTP simulado para verificar o contrato sem consumir API.

**Não declarar a equipe operacional por esta suíte.** Ainda exigem prova externa: chamada Gemini real para cada perfil; worker autenticado usando estado remoto; execução Docker real; tarefa conjunta real e retomada em outro runner. O `--preflight` verifica presença de configuração, não valida chave nem situação de cobrança Google. A implantação de plugins do ChatGPT é separada deste runtime: os 10 novos especialistas são agentes do executor, não plugins privados já instalados na conta.

## Especialistas

| Agente | Responsabilidade | ID executável |
| --- | --- | --- |
| May Maestro | Coordenar prioridades, dependências e orçamento; delegar trabalho com contratos verificáveis. | `may-maestro` |
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
3. O worker só será liberado quando houver secret `GEMINI_API_KEY` e variables `GEMINI_MODEL` com modelo disponível na conta, `GEMINI_FREE_TIER_CONFIRMED=true`, `HORIZON_WORKER_ENABLED=true`. A branch pode ser escolhida em `HORIZON_WORK_BRANCH`; o padrão é `feat/horizon-team-runtime-20261018`.
4. A atestação de free tier deve corresponder a um projeto Google **sem billing associado**. O cliente não possui API que comprove isso e nunca habilita billing nem troca para outro provedor. Uma assinatura Gemini/ChatGPT não configura este secret.
5. A tarefa roda com no máximo 12 chamadas por execução, 2048 tokens de saída por chamada e 3 tarefas por lote. Cota/429 resulta em retry com backoff; erro permanente fica registrado. Se o orçamento de chamadas acabar, preserva o checkpoint para outro lote.
6. Fila, memória e conteúdo de arquivos gerados são salvos em `agent-state/runtime`, via snapshot SQLite consistente. Checkpoint é confirmado remotamente depois de cada ação. O worker publica arquivos somente na branch autorizada, sem merge, sem deploy e sem force push. Se a branch mudou, para em conflito.

O workflow de verificação roda em push/PR sem API. A checagem de configuração em push informa apenas se chave/modelo/atestação existem, sem revelar valores. Chamadas reais não rodam em PRs. O agendamento horário (`17 * * * *`, UTC) só passa a existir depois da aprovação deste workflow na branch padrão. GitHub pode atrasar agendamentos; não há promessa de execução pontual ou processo 24/7. Sem tarefas não há chamada de IA. Não foi contratado nenhum recurso de hospedagem.

O runner padrão de um repositório público não cobra minutos de execução; não usamos runners maiores nem criamos serviços de nuvem. O workflow novo não cria artifacts de armazenamento: as evidências ficam nos logs e no estado versionado. Não se pode prometer gratuidade de uma chave Google associada a projeto pago; por isso a execução real fica bloqueada sem a verificação do operador.

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

A suíte verifica identidades e requisições independentes, leitura/memória por 17 perfis, ferramentas reais em fixtures Git, retomada SQLite após interrupção, DAG, dependências, falhas de QA preservadas, lease expirado, concorrência, orçamento, snapshot remoto simulado, proteção de caminhos e Docker simulado sem credenciais. Os sete auditores estáticos em `maestro_exec/` e `executors/` permanecem para compatibilidade; o caminho de codificação via Codex CLI foi retirado. `supervisor.py` encaminha ao runtime ativo; não contém mais o retorno antecipado desativando Gemini.

## Critério de ativação

Aprovar código na principal e confirmar a configuração gratuita não substituem a prova de operação. Para aceite final: executar um pedido pequeno com Gemini real; comprovar individualmente ferramenta, memória e checkpoint por especialista; executar uma cadeia real; interromper e retomar em outro runner; conferir branch, commit e resultados. Só então declarar os agentes operacionais. Até lá: código implementado e suíte automatizada, ativação externa pendente.

## Referências oficiais verificadas

- [Gemini generateContent](https://ai.google.dev/api/generate-content)
- [Billing Gemini](https://ai.google.dev/gemini-api/docs/billing)
- [Limites Gemini](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Runners GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)
- [Eventos/agendamentos Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
