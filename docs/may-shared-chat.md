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

## Algoritmos, confidencialidade e limites
- GitHub Issues são o transporte durável. Uma Issue por task-id e agent-id.
- Marcadores MAY_CONSULT_REQUEST_V2 e MAY_CONSULT_REPLY_V2.
- Somente perguntas textuais com payload.may_public_context_allowed=true
  aprovado pelo proprietário podem ser incluídas no GitHub público.
- Queda de ambos provedores só envia classe genérica, sem código, dados ou logs.
- Aguardando May: fila suspensa por 15 minutos (not_before), sem consumir
  tentativas ou bater repetidamente nos provedores.
- Até três perguntas sequenciais por agente/tarefa, com ticket SHA256 por rodada,\n  evitando loops de perguntas repetidas. Resposta apenas de comentário autenticado
  do proprietário, limites de tamanho, validação de esquema e controle de ACL.
- O agendador verifica até três tickets por execução, sequencialmente.
  Não criar um chat por agente. Criar sucessor somente após prova de
  limite/tamanho da conversa e atualizar a URL no agendador privado.
- O agendador do ChatGPT suporta execução no máximo horária; a ponte NÃO
  garante latência de segundos. Ausência de resposta nunca é sucesso.
- Os workflows externos continuam independentes do navegador remoto.
- Browser KERNEL tem teto de 72h DE INATIVIDADE por sessão, mas não concede
  tokens de uma API OpenAI ao executor.
