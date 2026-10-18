# May Maestro | Sete executores Horizon

Projeto de execucao real para os sete plugins privados da Horizon. Python 3.11+, sem dependencias de execucao para as auditorias. Cada agente possui entrada independente, escopo, testes, politica de edicao, checkpoint e evidencias.

## Agentes e responsabilidades

- **May Maestro:** roteamento, dependencias, registro e estado das tarefas.
- **Corregedoria May:** valida integridade dos checkpoints, rastreia faltas de evidencia e detecta risco de segredos.
- **May Persona:** revisa microcopy e consistencia de idioma de interfaces.
- **Horizon Skills:** inventaria arquitetura, scripts e testes de integracao.
- **Horizon Evolution Agent:** testa regressoes, estado de trabalho e integridade de edicoes.
- **Horizon Project Studio:** cataloga recursos e elabora dependencias entre tarefas.
- **Horizon UI Guardian:** verifica CSS, breakpoints, acessibilidade estaticamente e screenshots reais quando o navegador existe.

Cada arquivo executors/NOME.py pode ser invocado sozinho. Um processo tambem pode executar os sete papeis por meio de maestro_exec.runner --agent all. Essas execucoes compartilham motor comum, mas sao processos enderecaveis independentes; habilidades privadas nao viram processos autonomos sem runtime.

## Comandos verificaveis

    python3 -m unittest discover -s tests -v
    python3 -m maestro_exec.runner --list
    python3 -m maestro_exec.runner --agent all --root . --mode audit --branch feat/minha-tarefa
    python3 executors/horizon-ui-guardian.py --root . --mode audit --branch feat/minha-tarefa
    python3 executors/horizon-skills.py --root . --mode test --branch feat/minha-tarefa
    python3 -m maestro_exec.runner --verify evidencias/horizon-skills/checkpoint.json

Cada execucao registra evidencias/NOME/checkpoint.json com hash SHA-256. O GitHub Actions publica os resultados como artifacts persistentes por execucao. Nao sao commits automaticos.

Captura visual opcional: executar o Guardian com --url http://localhost:3050/ depois de iniciar um servidor local seguro. O runner aceita somente URLs localhost ou 127.0.0.1 e usa Chromium se disponivel. As capturas estaticas nao comprovam a interacao, autentificacao ou recursos Android.

## Execucao de codigo mediante aprovacao

Se o Codex CLI estiver **instalado e autenticado de verdade**, o executor com permissao de implementacao pode receber um arquivo de tarefa existente dentro do repositorio:

    python3 executors/horizon-skills.py --root . --branch feat/minha-tarefa --mode implement --task-file docs/tarefa.md --approve-branch-edits

Essa acao exige Git, worktree autenticada na branch permitida, autorizacao explicita e sandbox workspace-write; nao realiza push, merge ou deploy. Revisar as alteracoes e testes antes de publicar a branch. Os papeis Maestro e Corregedoria sao somente leitura.

A infraestrutura nao instala Codex, nao fornece credenciais de API nem permite que um plugin do ChatGPT convoque outro de forma implicita. Habilitar o runner remoto requer configurar os servicos que realmente o acionarao.

## Controle operacional

- NUNCA integrar a main, master, production ou release sem autorizacao especifica.
- Sem force push, deploy, exclusao de banco ou modificacao de segredos automaticos.
- O workflow usa somente contents: read e testa as branches isoladas.
- Checkpoints registram o que foi testado; indisponibilidade de Git, Chrome ou modelo nao e sucesso.
- Codigos/dados de cliente nao sao usados nos testes unitarios; somente fixtures temporarias.
- GitHub Actions trabalha por disparo e nao e um daemon 24/7. Um executor persistente exigira runtime e fila apropriados.

## Envolvimento no projeto de restaurantes

A branch feat/fluxo-mobile-premium-20261018 de danielnep/sistema-fluxo-restaurantes contem o novo CSS operacional. Nesta entrega, o fluxo de restaurantes foi testado em navegador com troca de perfis, confirmacao de turno e abertura de pedido, e a suite original teve 2 testes aprovados. A branch de executores e outro repositorio e nao deve ser confundida com o app.

