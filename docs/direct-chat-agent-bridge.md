# Canal direto Python: especialistas Horizon e May

**Teste em 20/10/2026:** o especialista registrado horizon-skills executou
um processo Python independente dentro da VM KERNEL. A CLI enviou check-in
para a conversa já existente Horizon | Central dos Agentes e leu resposta
real. Sem GitHub Actions, GitHub Issue, plugin do ChatGPT nem nova conversa.

## Componentes e limitações reais

- Cada um dos 17 agentes continua com seu próprio gateway Gemini/DeepSeek.
- O canal direto usa horizon_core/direct_chat.py para enviar e ler por ticket
  na conversa única. Não é a API OpenAI e não carrega credenciais ChatGPT.
- Python gerencia autorização, identidade, cooldown, persistência e respostas.
  **Playwright somente lê** a conversa e localiza o campo de mensagem; não
  chama `fill()` nem `click()` para enviar.
- Fora da VM, o envio usa a API oficial **KERNEL Computer Action**:
  `click_mouse`, `type_text`, verificação de rascunho e `press_key`.
  Na própria VM, Node/Playwright-Core usa CDP local **apenas para inspeção**,
  enquanto `xdotool` realiza mouse/teclado gráfico. O texto multiline local
  usa Shift+Enter; antes do Enter final o rascunho inteiro é comparado.
- **Histórico preservado:** o teste de 20/10 usou originalmente Playwright
  também para digitar/enviar. Não é prova de envio real pelo método novo;
  testes de regressão simulados confirmam apenas o contrato até nova prova
  autorizada na sessão autenticada.
- O GitHub Actions Worker continua necessário para executar aplicações,
  coordenar dependências, manter workspace e checkpoints. Ele não é obrigatório
  para transportar mensagens diretas ao ChatGPT.
- GitHub Issues mais automação horária ainda são o caminho de contingência
  quando o acesso direto estiver ausente. Um ticket de envio incerto jamais
  pode ser reenviado por outro canal sem conciliação.

## Comando independente na MESMA VM KERNEL

    python -m horizon_core.chat_direct_cli --agent horizon-skills --task-id proof-1 --checkin --local-browser

O Chrome autenticado permanece aberto. O processo só abre e fecha sua aba
isolada; não lê cookies nem exporta o endereço CDP.

Para recuperar sem reenviar:

    python -m horizon_core.chat_direct_cli --agent horizon-skills --task-id proof-1 --read --local-browser

## Processo remoto, por exemplo num runner GitHub

Exige KERNEL_API_KEY de escopo de projeto como secret do ambiente,
além do navegador may-chatgpt-72h autenticado no mesmo projeto.
A API HTTP documentada do KERNEL resolve pelo nome e continua válida
quando a sessão de 72h for renovada com o mesmo nome.

    python -m horizon_core.chat_direct_cli --agent qa-engineer --task-id qa-123 --question "What public README should QA read first?" --public-approved

O processo Python chama por HTTPS a inspeção de leitura do KERNEL e os
controles oficiais de computador na sessão nomeada. O executor serializado
`horizon-central` confere o chat, recusa composer ocupado, localiza o campo
e confirma a digitação antes de pressionar Enter. Erro após interação gráfica
é entrega incerta, não autorização para reenviar. O marcador do ticket
vincula a resposta ao pedido, nunca apenas à última mensagem arbitrária.

## Rotas do Worker e prova

Se KERNEL_API_KEY estiver efetivamente disponível no processo do Worker,
a consulta a May usa primeiro o transporte direto. A resposta volta como
dado não confiável, validado contra o contrato de ferramentas e caminhos
permitidos ao especialista. Sem credencial ou com indisponibilidade confirmada,
o GitHub Issues continua sendo fallback. Entrega ambígua mantém o ticket
pendente e evita envio duplicado. O Worker conserva o checkpoint e retoma sem
perder a pergunta ou repetir consultas Gemini/DeepSeek.

Limite: quatro consultas por especialista em janela móvel de uma hora, com
memória persistente local para o canal direto e limite já existente para
GitHub Issues. Não há teto definitivo por tarefa; evitar tratar o contador
de ambos os transportes como perfeitamente unificado se alternarem.

Não gravar KERNEL_API_KEY em Git, logs, screenshots, prompts, issues ou artefatos.
A chave permite controlar todo o projeto KERNEL, por isso sua distribuição
a hosts externos deve ser restrita e auditável. Não exigir API OpenAI nem
afirmar que configurar uma referência ao secret instala seu valor.
