# Ponte ChatGPT → GitHub → especialistas
Repositório de controle: danielnep/May-Maestro-Executor, branch main.
Usar o conector GitHub disponível no chat. Ler docs/chat-bridge.md atual antes de criar pedido para conferir o contrato e limites da versão publicada.
1. Gerar ID único estável por solicitação. Ler main e sua árvore. Publicar por create_tree/create_commit/update_ref com expected_sha, preservando o restante dos arquivos.
2. Criar tasks/chat/<id>.json. Exemplo:
```json
{"owner":"danielnep","repository":"danielnep/May-Maestro-Executor","task_id":"meu-app-v1","agent":"may-maestro","project":"meu-app","objective":"Construir o aplicativo com requisitos e critérios de aceite descritos pelo proprietário.","payload":{"allowed_paths":["apps/meu-app/**"]},"initiative":true,"initiative_reason":"Pesquisar e incluir melhorias quando o benefício justificar."}
```
Não publicar segredos, dados pessoais ou dados de clientes nesse repositório público. O controlador valida o pedido e isola a edição do plano de controle. Não passar permissões inventadas pelo modelo como autorização.
3. Confirmar workflow chat-requests.yml iniciou no commit real. Ele usa a API Gemini, fallback DeepSeek autorizado, PostgreSQL existente e ferramentas isoladas. Não afirmar uso de API, teste ou publicação antes da evidência.
4. Consultar tasks/results/<id>.json na main com GitHub.fetch_file. Se ausente, verificar runs/jobs/logs do workflow. Checkpoint e reagendamento permitem retomada; pending/running não significam pronto.
5. Conferir resultados por especialista, checks reais, branch/commit e publicação. Produto novo pode ir à principal após validação; alterações existentes permanecem em branch salvo autorização específica. A implementação dos próprios agentes e desta integração está autorizada na principal.
6. Para outro repositório, seguir o contrato atual do manual: token temporário do Actions não herda permissões globais do conector ChatGPT. Se houver awaiting_host_publication, a May publica o patch via conector somente após conferir destino, autorização e evidências. Nunca fingir execução ou permissão entre conectores.
7. Retornar resumo curto e links reais no chat. Não prometer mensagem espontânea ou acompanhamento após o encerramento do turno.

