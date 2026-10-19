# Pedidos do chat por commit

Esta ponte usa o conector GitHub já autorizado no chat. May grava um JSON em
`tasks/chat/<id>.json` na principal; o push aciona Actions. Não exige CLI no
telefone, PAT permanente, OAuth adicional nem credencial dentro do pedido.

O controlador é código confiável da principal. Valida proprietário, repositório,
especialista e namespace; transforma o JSON em tarefa na fila SQLite com checkpoints criptografados no GitHub.
Cada pedido recebe projeto isolado e branch `agent/chat-<id>`. Em `new_product`
(padrão), os especialistas editam somente o namespace `apps/<project>/`. Em
`existing_product`, editam a lista explícita de arquivos/padrões autorizados.
Esse escopo é herdado nas delegações e nunca ampliado pelo modelo. A fila é
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

Para corrigir um produto existente, use `delivery_type: "existing_product"` e
declare `payload.allowed_paths` obrigatoriamente. Exemplo:

```json
{
  "owner": "danielnep",
  "repository": "danielnep/app-hondas-maxvel",
  "task_id": "ronda-interface-001",
  "delivery_type": "existing_product",
  "agent": "may-maestro",
  "project": "ronda-pro",
  "objective": "Revisar e corrigir a interface somente nos caminhos aprovados; preservar backend, testar e entregar evidências verificáveis na branch de trabalho.",
  "payload": { "allowed_paths": ["src/**", "index.html"] }
}
```

Os caminhos acima são exemplo de contrato: May deve conferir os arquivos reais
do repositório antes de enviar. Essa modalidade **sempre entrega em branch de
trabalho**, mesmo se todos os testes passarem. Publicar na principal depende da
aprovação separada e da ação do host; o pedido não cria essa autorização.
Não são aceitos caminhos ocultos, absolutos, com `..`, segredos ou glob na raiz
(`**`, `*.js`). No repositório do controlador também são protegidos seus agentes,
runtime, SDK/sandbox, ferramentas Android, workflows, testes e fila de controle.
Aplicativos Android externos podem autorizar `android/...` explicitamente;
configurações locais, keystores e credenciais permanecem proibidos.

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

O cron de cinco minutos é apenas a recuperação de segurança e pode sofrer atraso do GitHub. Um novo pedido em `tasks/chat/*.json` já dispara o workflow por push, sem esperar esse cron. Quando a sessão de 40 minutos encerra com tarefas imediatamente executáveis, o controlador salva `runtime/next-session.json`, e a última etapa do job chama a API oficial `workflow_dispatch` para iniciar a próxima execução automaticamente. O GitHub serializa essas sessões no grupo `horizon-durable-worker`; nenhuma sessão concorrente retoma a mesma fila. Trabalho em backoff, bloqueado ou ainda reservado por lease não provoca disparos em loop. Erro antes da publicação do marcador ou indisponibilidade da API deixa o cron de cinco minutos como recuperação. O handoff não reinicia o trabalho desde o início, não relança ferramentas completadas e não usa PAT permanente. O grupo
`horizon-durable-worker` é compartilhado com o coordenador existente para evitar
concorrência de publicações. Sem pedidos não ocorre chamada de IA.

`repository` pode indicar outro repositório **público** de `danielnep`. O controlador
prepara um checkout separado e verifica a identidade real antes de trabalhar.
Produtos novos usam namespace novo; produtos existentes exigem escopo explícito.
Repositórios privados e outras contas não fazem parte desta ponte inicial.
O token temporário do Actions geralmente escreve apenas no repo do
workflow. Quando o GitHub recusa escrita no destino externo com 403/404, o resultado
fica `awaiting_host_publication`, com arquivos completos em `publication.patch_files`,
hash anterior, alvo e commit base. May deve verificar o destino e publicar usando
seu conector GitHub, depois confirmar o commit real. Esse estado **não significa
publicado** e não transfere credenciais do chat para o worker.

Como o repositório é público, pedidos e resultados devem conter somente dados
sintéticos ou públicos. Os novos checkpoints são arquivos SQLite comprimidos e criptografados em branch pública; o histórico SQLite anterior permanece no repositório e deve ser tratado como público. Não importar dados reais nem informações privadas de clientes. Os valores de Gemini, DeepSeek e Railway ficam apenas nos secrets do
coordenador. O token GitHub é temporário do job; não entra no sandbox de testes.

Ativação: aprovar os arquivos desta ponte na principal e manter os secrets e o
secret de criptografia `HORIZON_SQLITE_ENCRYPTION_KEY` (ou fallback temporário para o token Railway existente, sem rotacioná-lo sem migração). Um commit que inclua o workflow dispara a
primeira execução; `workflow_dispatch` permite retomada manual sem terminal.
Testes locais do contrato: `python -m unittest discover -s tests -p test_commit_bridge.py -v`.

## Resiliência e aprendizado verificado (19/10/2026)

O backend ativo da ponte é `github-sqlite`. Ele restaura checkpoints da branch `agent-state/runtime` (formato gzip e criptografia Fernet, com fallback legado somente para leitura inicial), verifica integridade e salva por CAS. A execução não recorre ao PostgreSQL cheio: a base antiga no Railway fica intacta, **não reconciliada automaticamente** com o snapshot SQLite histórico. Erros de gravação interrompem o worker, não produzem falso checkpoint nem fallback silencioso.

Antes da primeira chamada do modelo, o trabalhador executa inventário do projeto por algoritmo, sem rede. Erros de ferramentas e validações são classificados e deduplicados na memória separada `__agent_learning__:<agente>`, com regras preventivas inicialmente marcadas `observed`. Uma falha de teste só muda para `verified_by_matching_check` depois que a mesma checagem passa na mesma tarefa; não significa treinamento dos pesos do modelo. Retentativas automáticas são restritas a leituras com falhas transitórias. O mecanismo não executa comandos ou alterações desnecessárias para alcançar uma 'autocorreção' aparente.

## Prévia automática de branch via GitHub Pages

O proprietário autorizou que a página GitHub Pages de cada projeto web reflita a **última branch atualizada**, sem modificar `main`. Após o controlador publicar um commit em `agent/chat-...`, ele consulta o Pages do destino e atualiza a origem para essa branch. O resultado aparece em `publication.pages_preview` no recibo JSON. Status comprovados: `verified` (trocado e lido novamente), `already_current`, `pages_not_enabled` (projeto sem Pages), `needs_pages_admin_token`, `pages_update_failed`, `workflow_mode_requires_explicit_deployment`.

Requer criar **`HORIZON_PAGES_TOKEN`** como secret do executor, com acesso aos repositórios de destino e permissões GitHub de `Pages:write` e `Administration:write`. O `GITHUB_TOKEN` padrão não concede Administração, e a automação jamais alega ter mudado o Pages se não obteve o GET confirmatório. Para commits feitos manualmente via conector, a assistente aplica a mesma sincronização em ferramenta com acesso a Pages. Em projetos que têm Pages em modo GitHub Actions, não substitui o workflow de deploy silenciosamente. Repositórios dinâmicos/backends não são convertidos em sites estáticos.
