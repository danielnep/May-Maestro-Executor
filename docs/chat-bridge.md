# Pedidos do chat por commit

Esta ponte usa o conector GitHub já autorizado no chat. May grava um JSON em
`tasks/chat/<id>.json` na principal; o push aciona Actions. Não exige CLI no
telefone, PAT permanente, OAuth adicional nem credencial dentro do pedido.

O controlador é código confiável da principal. Valida proprietário, repositório,
especialista e namespace; transforma o JSON em tarefa na fila PostgreSQL privada.
Cada pedido recebe projeto isolado e branch `agent/chat-<id>`. Os especialistas
editam somente o namespace `apps/<project>/`, herdado nas delegações. A fila é
reivindicada atomicamente por projeto; retomar não consome tarefa de outro checkout.

Exemplo para `tasks/chat/reservas-proof-v1.json`:

```json
{
  "owner": "danielnep",
  "repository": "danielnep/May-Maestro-Executor",
  "task_id": "reservas-proof-v1",
  "agent": "may-maestro",
  "project": "chat-bridge-proof",
  "objective": "Coordene Backend Engineer e QA Engineer em etapas dependentes para criar apps/chat-bridge-proof, um motor Python stdlib de reservas com capacidade finita por recurso, chave idempotente por cliente, rejeição de replay conflitante e cancelamento sem duplicar devolução. Escreva testes determinísticos de conservação de capacidade, idempotência, conflito, isolamento entre clientes e cancelamento repetido. Use somente arquivos no namespace aprovado. O QA deve executar testes reais por run_check no diretório do aplicativo quando suportado; informe bloqueio em vez de inventar aprovação. Registre entrega, testes e pendências. Não mexa no controlador, credenciais ou sistemas de clientes.",
  "payload": {
    "allowed_paths": ["apps/chat-bridge-proof/**"]
  }
}
```

O ID identifica pedido imutável: repetir o arquivo não cria outra tarefa. Alterar
seu conteúdo depois da importação é rejeitado; uma mudança de objetivo usa ID novo.
`initiative=true` exige `initiative_reason` concreta e não amplia permissões.

O resultado aparece em `tasks/results/<id>.json`, com estados dos especialistas,
resumos, verificações reais e resultado de publicação. May lê esse arquivo ao
consultar a execução e devolve os links ao chat. Não existe envio espontâneo a
uma conversa que não forneça essa capacidade.

A principal só recebe um aplicativo novo se a política determinística comprovar
namespace novo, tarefas concluídas, testes reais do conteúdo atual e baseline
inalterado. Na falta dessas condições, a entrega permanece na branch de trabalho.
Nenhum deploy de produção é autorizado pela ponte. O relatório diferencia relato
do agente, teste executado e publicação; nunca presume aprovação pelos textos.

O cron horário retoma pendências e pode sofrer atraso do GitHub. O grupo
`horizon-durable-worker` é compartilhado com o coordenador existente para evitar
concorrência de publicações. Sem pedidos não ocorre chamada de IA.

`repository` pode indicar outro repositório **público** de `danielnep`. O controlador
prepara um checkout separado e verifica a identidade real antes de trabalhar;
continua limitado a um namespace novo `apps/<project>/`. Repositórios privados,
outras contas e alterações livres de aplicativos existentes não fazem parte desta
ponte inicial. O token temporário do Actions geralmente escreve apenas no repo do
workflow. Quando o GitHub recusa escrita no destino externo com 403/404, o resultado
fica `awaiting_host_publication`, com arquivos completos em `publication.patch_files`,
hash anterior, alvo e commit base. May deve verificar o destino e publicar usando
seu conector GitHub, depois confirmar o commit real. Esse estado **não significa
publicado** e não transfere credenciais do chat para o worker.

Como o repositório é público, pedidos e resultados devem conter somente dados
sintéticos ou públicos. Memória completa e checkpoints permanecem no banco
privado. Os valores de Gemini, DeepSeek e Railway ficam apenas nos secrets do
coordenador. O token GitHub é temporário do job; não entra no sandbox de testes.

Ativação: aprovar os arquivos desta ponte na principal e manter os secrets e o
PostgreSQL privado já configurados. Um commit que inclua o workflow dispara a
primeira execução; `workflow_dispatch` permite retomada manual sem terminal.
Testes locais do contrato: `python -m unittest discover -s tests -p test_commit_bridge.py -v`.
