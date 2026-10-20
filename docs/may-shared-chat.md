# Horizon | Central única dos agentes (19/10/2026)

## Fluxo e agentes
O Maestro coordena a fila, mas qualquer um dos 17 especialistas pode consultar
May e precisa identificar o agente e um ticket. Cada especialista conserva
Gemini e DeepSeek próprios e pode usar May como terceira opção de raciocínio.

Uma só conversa de ChatGPT, intitulada 'Horizon | Central dos Agentes', é
monitorada pelo proprietário. A URL do chat é mantida somente no agendador
privado, NUNCA nas Issues ou repositório público.

## Envelope das mensagens
[HORIZON CONSULTA V2]
AGENTE: agent-id
TICKET: identificador-hash
TIPO: specialist_question ou providers_unavailable
PERGUNTA: texto técnico explicitamente público

May responde com conselho verificável ou UM único JSON:
{"action":"read_file","args":{"path":"README.md"},"summary":"..."}
A decisão só é aceita pelo worker se estiver dentro das ferramentas reais
autorizadas para aquele agente, e a verificação da ferramenta ocorrer.

## Padrão de entrega pós-projeto ou correção (canal existente)

O fluxo de mensagens do chat dedicado **já foi testado com sucesso**. Não criar outra conversa ou afirmar que os agentes não conseguem falar com a May. Quando o trabalho tiver código e testes concluídos, reutilizar a mesma ferramenta de envio para comunicar `AGENTE`, `TICKET`, `TIPO: delivery_review`, link público *verificado* de prévia, SHA e 3–5 achados. May abre o link, testa fluxos e motion e responde brevemente; agente corrige e repete o teste.

`tasks/results/<id>.json` com `may_review` fornece os dados para o envio. **O arquivo sozinho não envia mensagem**. Após `publish_result`, `commit_bridge` invoca `delivery_chat.queue_review` **somente** se o QA estiver aprovado e `may_review.status=ready_for_may_review`, com URL pública de Pages e SHA confirmados. Cria uma única GitHub Issue `[May delivery]` / `MAY_DELIVERY_REVIEW_V1` por tarefa+commit, com metadados públicos. A tarefa horária EXISTENTE `Central Horizon dos Agentes` verifica a fila, posta `[HORIZON ENTREGA V1]` neste MESMO chat autenticado pelo KERNEL e só grava `MAY_DELIVERY_SENT_V1` na Issue após confirmar a mensagem e a resposta real. Enquanto não existir esse comprovante, o ticket permanece aberto e o envio não é considerado concluído. A cadência horária não assegura entrega instantânea. Consultas técnicas já em funcionamento não são prova de que esse novo gatilho foi automatizado. Capturas privadas não vão para canal público.

## Navegação na interface da Central (sem API)
A automação horária deve descobrir a sessão autenticada pelo nome
`may-chatgpt-72h` e verificar a URL exata da conversa. Tentar ações nativas
WebMCP, quando o site as oferecer; na ausência delas, usar o **KERNEL Computer
Action** para clicar, digitar, rolar e enviar pelo navegador gráfico real.
Playwright pode auxiliar na leitura de estado e na seleção da guia existente;
não criar chats novos nem tentar simular uma API HTTP por cookies internos.
A cada ação gráfica, capturar o estado final, conferir a mensagem no histórico
e só então avançar o cursor por SHA/CAS no GitHub. Em falha, preservar o texto
não enviado, cursor e Issues. A captura gráfica requer dimensões válidas da
janela; se faltarem, configurar a viewport na própria sessão existente,
sem trocar nem reiniciar seu perfil autenticado. Nenhum destes controles
autoriza contornar CAPTCHA, bloqueios de segurança ou limites do site.

## Algoritmos, confidencialidade e limites
- GitHub Issues são o transporte durável: cada rodada tem ticket único por
  task-id, agent-id e índice, preservando idempotência.
- Marcadores MAY_CONSULT_REQUEST_V2 e MAY_CONSULT_REPLY_V2.
- Somente perguntas textuais com payload.may_public_context_allowed=true
  aprovado pelo proprietário podem ser incluídas no GitHub público.
- Queda de ambos provedores só envia classe genérica, sem código, dados ou logs.
- Aguardando May: fila suspensa por 15 minutos (not_before), sem consumir
  tentativas ou bater repetidamente nos provedores.
- Quatro consultas por agente numa janela móvel de 60 minutos, contando
  Issues V2 criadas no GitHub, inclusive de tarefas distintas. A quinta espera
  até haver vaga; pergunta, rodada e checkpoint permanecem intactos.
  Após o intervalo, a tentativa seguinte envia o mesmo ticket sem usar
  outra chamada Gemini/DeepSeek. Não existe limite vitalício por tarefa.
- Resposta: uma única ação JSON executável ou um diagnóstico curto;
  selecionar primeiro a leitura/teste que falta quando houver incerteza,
  sem escrever prosa genérica nem inventar resultados. O executor valida ACL.
- O agendador verifica até três tickets por execução, sequencialmente.
  Não criar um chat por agente. Criar sucessor somente após prova de
  limite/tamanho da conversa e atualizar a URL no agendador privado.
- O agendador do ChatGPT suporta execução no máximo horária; a ponte NÃO
  garante latência de segundos. Ausência de resposta nunca é sucesso.
- Os workflows externos continuam independentes do navegador remoto.
- Browser KERNEL tem teto de 72h DE INATIVIDADE por sessão, mas não concede
  tokens de uma API OpenAI ao executor.
