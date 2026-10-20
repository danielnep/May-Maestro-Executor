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


## Pesquisa aprofundada: engenharia frontend e UI/UX (estudo de 15/10/2026)

Fonte: síntese seletiva do relatório entregue pelo proprietário em 19/10/2026, intitulado "Front-end para aplicações, sites e sistemas complexos: estudo aprofundado de UI, UX, arquitetura e engenharia". Estas são diretrizes de investigação e implementação, não autorização para reescrever produtos, alterar framework, instalar serviços ou contrariar a identidade visual já aprovada.

### Escolha de arquitetura por cenário
- Site público, conteúdo e SEO: avaliar HTML inicial útil, SSG/SSR/híbrido, scripts seletivos e cache. CRM, COI, ERP e painéis internos: SPA/CSR ou híbrido pode ser adequado; priorizar latência de tarefas, filtros, sessões longas e acessibilidade. Não impor SSR a um painel sem motivo.
- PWA/offline: instalabilidade isolada não basta. Desenhar Service Worker, IndexedDB/Cache Storage, sincronização e resolução de conflitos com feedback offline, sincronizando, sincronizado, erro e conflito quando o produto exigir.
- Android/iOS: diferenciar APK nativo, app híbrido WebView e web/PWA. Preservar empacotamento e tecnologia já aprovados; comparar Compose, React Native, Flutter e Capacitor somente quando houver decisão arquitetural autorizada.
- Escala: preferir monólito frontend modular, organizado por domínios reais, não micro-frontends por tamanho aparente. Micro-frontends exigem justificativa organizacional de equipes/deploys independentes, com custo de dependências, debugging e testes.
- Multiplataforma: compartilhar design tokens, contratos de API, termos de domínio, ícones e validações portáveis, sem forçar um renderer único.

### Organização de código e dados
- Separar estado local de UI (modal/input/seleção), estado da aplicação (sessão/workflow/permissões), estado do servidor (consulta/cache/paginação/invalidação) e armazenamento offline. Evitar duplicar respostas da API em stores globais.
- Organizar por domínio; manter camada UI separada de application/model/api/tests. Usar clients tipados e contratos existentes (OpenAPI/GraphQL onde disponíveis). Não espalhar chamadas HTTP ou regras críticas do servidor em componentes.
- Segurança de interface: esconder botão não implementa autorização; servidor deve validar permissões. Não embutir segredos, tokens ou dados privados no bundle; tratar entrada e HTML dinâmico como não confiáveis; proteger CSP/CSRF conforme stack.

### UX funcional, adaptabilidade e motion
- Projetar para largura, altura, orientação, densidade, zoom, safe areas, toque/mouse/teclado e capacidade do aparelho. Um simples sinalizador mobile não demonstra adaptabilidade. Adotar layouts compactos ou expandidos por conteúdo, como list-detail, quando fizer sentido.
- Baseline web WCAG 2.2 AA: HTML semântico antes de ARIA, labels e erros associados, foco visível/restaurado, acesso por teclado, contraste, leitores de tela conforme plataforma e reflow por volta de 320 CSS px sem perda de função.
- Design system é contrato de comportamento e aparência: tokens semânticos para superfícies, tipografia, spacing, estado, foco e movimento. Preservar paletas atuais de cada projeto e a regra Horizon contra cards decorativos e CSS corretivo acumulado.
- Microinterações devem esclarecer ação → feedback → novo estado. Testar abrir, fechar, cancelar, gestos, scroll, toque, foco e redução de movimento. Animação nunca deve deslocar alvos de clique nem mascarar travamentos.

### Aceite e evidência
- Performance web quando mensurada: 75º percentil com LCP ≤ 2,5 s; INP ≤ 200 ms; CLS ≤ 0,1. Observar também tempo até conteúdo útil, latência de pesquisa/salvar, bundle, erros JS e long tasks. Sem instrumento real, não inventar números.
- Cobrir loading, empty, error, disabled, success, offline e conflito quando aplicáveis; teclado, zoom, rolagem, textos longos e viewports compactos/expandidos.
- Testes escalonados conforme risco: lógica unitária, componentes, integração, E2E crítico, regressão visual e acessibilidade automatizada + manual. Playwright prova interações em browser; print sozinho não comprova gesto, uso de leitor de tela ou teste físico Android.
- CI/telemetria: registrar apenas evidências efetivas de lint, types, build, testes, prévia e erros por versão. Não coletar credenciais, conteúdo de formulários ou dados pessoais nos traces.

### Aplicação por competência
- Frontend Engineer: state/API boundaries, composição, rendering por rota, estado de interface e performance verificada.
- UI/UX Designer: jornada, hierarquia, tokens, layout adaptável e especificação verificável de motion/estado.
- Horizon UI Guardian: evidência de clipping, foco, gesto e responsividade a partir da interface real.
- Mobile App Builder: lifecycle, offline, permissões, ergonomia e teste real de recursos físicos.
- Software Architect: ADR enxuto para domínio/rendering/state/API e riscos de replatforming.
- QA Engineer: testes de fluxos, regressão, acessibilidade e métricas medidas, nunca presumidas.

Documentos oficiais mencionados na pesquisa: WCAG em https://www.w3c.br/traducoes/wcag/wcag22-pt-BR/ ; Core Web Vitals https://web.dev/articles/vitals ; Playwright https://playwright.dev/ ; Android UI https://developer.android.com/develop/ui ; OWASP https://cheatsheetseries.owasp.org/cheatsheets/Web_Frontend_Security_Cheat_Sheet.html ; React https://react.dev/ . Links são referências, não testes executados.
