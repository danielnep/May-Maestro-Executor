# Referências reais de excelência em UI/UX (19/10/2026)

Esta biblioteca integra o material de referência dos especialistas via registry.py. **Não copiar layout, marca, imagens ou código de terceiros.** Examinar screenshots nas páginas oficiais e extrair princípios aplicáveis à tarefa. Links são referências externas; não são screenshots capturados e anexados ao repositório.

## Linear, sistemas operacionais empresariais
- Capturas e análise oficial (2026): https://linear.app/now/behind-the-latest-design-refresh
- Redesign e imagens de navegação: https://linear.app/now/how-we-redesigned-the-linear-ui
- Observar: hierarquia de headers, sidebar, alinhamentos, densidade, visibilidade de ações, redução de ruído.
- Aplicação: COI, Administração, dashboards e aplicações B2B.

## Things 3, aplicativos móveis
- Imagens oficiais: https://culturedcode.com/things/
- Atualização visual e screenshots iPhone/iPad/Mac: https://culturedcode.com/things/blog/
- Observar: tarefas sem cards decorativos, conteúdo contínuo, ações contextualizadas, microinterações sutis, tipografia.
- Aplicação: interfaces de campo, garçons, vigias e listas de tarefas.

## Flighty, mapas e informação em tempo real
- Imagens oficiais: https://flighty.com/
- Estudo e capturas Apple Design Award: https://developer.apple.com/news/?id=970ncww4
- Observar: hierarquia de alertas, mapa legível, informações críticas na primeira leitura, densidade operacional e feedback contextual.
- Aplicação: Ronda Pro, mapas, rotas, status e notificações.

## Raycast, comandos e acabamento
- Interface e imagens oficiais: https://www.raycast.com/
- Redesign e imagens explicadas: https://www.raycast.com/blog/a-fresh-look-and-feel
- Observar: comandos rápidos, barra contextual, coerência de ícones, atalhos e estados compactos.
- Aplicação: pesquisa, ações frequentes, operações de teclado e ferramentas administrativas.

## Motion premium: especificação e verificação, não enfeite

- Abrir e fechar são um único fluxo: origem, entrada, estado de repouso, saída, reversão, foco e eventual gesto de dismiss coerentes. Feedback do toque/clique deve ser imediato. Não aceitar simplesmente uma janela que aparece, uma cruz no topo e nenhuma transição de saída.
- Estabelecer hierarquia temporal: resposta rápida ao toque, transição perceptível porém ágil, deslocamento com direção/causalidade, easing coerente, duração proporcional à importância. Evitar efeito genérico repetido em cada componente e *idle loops* que deslocam controles e impedem clique.
- Painéis móveis devem respeitar arrasto no corpo válido e não conflitar com scroll, mapa, teclado ou botões; avaliar movimento tanto no Chrome mobile quanto em dispositivo real quando houver interface Android.
- Controles de fechar não se resumem a "X": considerar voltar contextual, swipe-to-dismiss, ação cancelar/concluir, cabeçalho integrado e Esc/Voltar Android. Se X for semanticamente necessário, integrar com o design e acessibilidade, nunca adicioná-lo como assinatura automática.
- Julgar o produto por jornadas conectadas, 320–393 px no telefone, desktop, teclado, safe area, dark/light conforme tema, contraste, ícones vetoriais proporcionais, espaço negativo e coerência entre telas. Uma imagem bonita não prova gesto, transição ou resultado.

## Rubrica de qualidade (0 a 4 por critério)
Avaliar: (1) hierarquia e foco, (2) composição sem cardificação, (3) tipografia/ritmo/alinhamento, (4) adequação mobile e alvos, (5) clareza operacional e estados, (6) consistência com identidade aprovada, (7) acessibilidade/contraste, (8) microinterações e resposta, (9) responsividade real, (10) implementação sem regressão. Registrar evidência por tela e viewport. Nota alta exige renderização e testes reais, nunca somente intenção ou screenshot de referência.

## Critério visual específico
Evitar **'xizinhos' decorativos** e botões de fechar repetidos sem função. Controle de fechamento só onde existe modal, drawer, painel dispensável ou estado realmente fechável, com semântica e acesso adequados. Não criar widgets flutuantes, chips, ícones e contornos apenas para parecer tecnológico. Luxo é precisão, não ornamentação. Em sistemas críticos, priorizar previsibilidade e operação.
