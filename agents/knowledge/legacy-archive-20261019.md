# Horizon | Lições técnicas recuperadas do estado legado (19/10/2026)

Origem: snapshot privado Railway PGlite, revisão 362. O snapshot completo NÃO foi publicado neste repositório público; está criptografado em backup privado do proprietário, acompanhado de chave de recuperação independente. O estado ativo permanece o SQLite criptografado `agent-state/runtime`; este arquivo **não** substitui a fila operacional nem recupera automaticamente tarefas legadas.

## Lições verificáveis e reutilizáveis

- **Upload ≠ interpretação de planilha**: no projeto de dashboard Sigware/COI, a rota autenticada `PUT /api/files/:id` valida identificador/hash, grava binário e metadados. Os dados de dashboard vêm do `POST /api/store/batch`. A IA por `POST /api/michelle-ai` recebe contexto preparado pelo cliente; não analisa automaticamente a planilha enviada. É necessário pipeline de servidor upload → validação → parsing → classificação → proposta de dashboard e testes de integração. Referência de origem: investigação do agente May Maestro em 18/10, baseada em leitura de `server/index.mjs` e `michelle-ai-client.js`. Confirmar código atual antes de agir.
- **QA sem Docker disponível não equivale a teste aprovado**: teste `node-test` falhou em ambiente `docker-web` por `pull access denied` para imagem de prova. Um teste Node direto em runner, com fixture sintética, é alternativa; manter erro original registrado. Registro do QA Engineer em 18/10.
- **Publicação não pode ser inferida da conclusão dos especialistas**: o controlador continha status `awaiting_host_publication`. Exigir SHA publicado, testes relevantes e inspeção da branch antes de sinalizar entrega. Confirmar estado atual por `tasks/results`.
- **Não misturar arquivos de workspace de projetos com o armazenamento da fila**: no arquivo legado havia tarefas, eventos, memória e trechos de código de trabalho. Snapshot bruto e payloads de cliente permanecem em backup privado; GitHub público só recebe sínteses técnicas revisadas.
- **Persistência temporária separada de memória permanente**: SQLite da execução e handoffs são transitórios; decisões e métodos técnicos passam para `agents/knowledge/` após revisão, com origem e critério de regressão.

## Inventário e cadeia de custódia

- Revisão: `362`; checksum SHA-256 completo do SQLite: `324b49bf4bffcf12e7c68295b436d002f70cfea02018d8f40f376c14894fab6b`
- Snapshot lógico: 2.199.552 bytes, `PRAGMA quick_check=ok`, 19 tarefas / 302 eventos / 20 registros de memória.
- Backup do proprietário: `/Horizon/Backups/horizon-legacy-recovery-20261019.zip` na biblioteca privada ChatGPT; chave de recuperação em `/Horizon/Backups/horizon-legacy-recovery-private-20261019.pem`, **nunca** publicar essa chave.
- Restauração testada em ambiente isolado: descriptografia AES-256-GCM, chave encapsulada RSA-3072 OAEP, descompressão, checksum SHA-256 e `PRAGMA quick_check=ok`.
- O GitHub Actions artifact original expira; a cópia privada não depende dessa retenção.
- As tarefas antigas **não foram mescladas automaticamente** ao estado ativo. Caso precisem de retomada, reconciliar IDs e estados para não duplicar execução.
