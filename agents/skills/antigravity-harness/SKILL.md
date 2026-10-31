# Antigravity SDK | Horizon: engenharia de agentes verificavel

Fonte tecnica: https://github.com/google-antigravity/antigravity-sdk-python
Documentacao: https://antigravity.google/docs/sdk/overview
Publicacao oficial: https://antigravity.google/blog/introducing-google-antigravity-sdk

## Aprendizado utilizavel
O SDK Python do Google Antigravity reune um runtime de agentes, ferramentas
de arquivos/terminal, MCP, skills, policies/hooks, streaming, sessoes com
continuidade e subagentes com contexto isolado. Sao interfaces diferentes
do proprio Antigravity IDE/CLI; um clone do GitHub NAO substitui o
runtime binario de `pip install google-antigravity`.

## Decisoes de integracao Horizon
- Preservar Maestro como dono de pedidos, fila, prioridades, aprovacao,
  checkpoint SQLite, recuperacao, QA e politica de publicacao.
- Invocar o SDK como executor OPCIONAL e isolado por tarefa/branch,
  nunca como segundo Maestro nem substituindo todo o worker.
- Vincular cada execucao a agent_id, task_id, project, branch, estado
  da fila, artefatos e saida de ferramentas. Nao dizer que subagente
  concluiu se so houve delegacao.
- Streaming de eventos de ferramentas e conclusao deve alimentar o
  painel em tempo real; nao simular progresso por temporizador.
- Exigir evidencias antes de publicacao: diff, testes, screenshots
  do navegador/Android quando aplicavel e revisao independente.
- O SDK usa Gemini por padrao. Antes de habilitar, conferir cotas,
  modelos disponiveis, custos e separacao de credenciais por projeto.
  Nao extrapolar que ele respeita automaticamente a rota Gemini>Groq>
  Gemma>Qwen configurada no Maestro.
- Usar policies de menor privilegio e workspaces isolados. Nao usar
  allow_all para o worker, nem expor a VPS pessoal, arquivos .env,
  chaves, SSH, Docker socket ou banco de clientes a agentes.
- Nao enviar codigos privados nem dados de clientes a modelos sem
  consentimento apropriado. Segredos sempre fora do Git.
- Padrão de smoke test: instalar em venv separada, importar Agent e
  LocalAgentConfig, verificar runtime compilado, rodar doctor sem IA,
  depois teste com tarefa consentida, medir uso e registrar logs
  redigidos. Sem chamadas pagas implicitas no teste offline.

## Resultado e limites
A existencia de classes Python e do binario compilado comprova instalacao,
nao autonomia de ponta a ponta. Publicar apenas com testes funcional, de
seguranca e regressao efetivamente realizados.
