---
name: meticulosidade
description: "Habilidade operacional obrigatória de engenharia, design, testes e entrega dos 17 especialistas Horizon, com critérios de acabamento, causalidade e prova."
version: 1.0.0
---

# Meticulosidade | Habilidade transversal dos 17 especialistas

## Definição operacional
**Meticulosidade** é a disciplina de examinar as partes e as relações do produto com precisão suficiente para descobrir defeitos que uma verificação superficial deixaria passar, corrigi-los na origem e comprovar que a correção funciona sem estragar o restante. É **atenção sistemática demonstrável**, não perfeccionismo ornamental, demora sem propósito ou a expressão "revisado cuidadosamente".

Para cada mudança: **observar → reproduzir → localizar a causa → corrigir na fonte → testar a ação real → verificar efeito colateral → registrar evidência → aceitar ou reabrir**. Uma etapa só está pronta quando as condições relevantes forem avaliadas. Diferenças de ambiente e testes não executados devem permanecer explícitos.

## Processo obrigatório em todas as funções
1. **Inventário e estado inicial:** recuperar contrato, decisão mais recente, árvore de dependências, branch/commit, comportamento aprovado e falhas pré-existentes. Consultar melhorias já feitas em branches e PRs; não reescrever nem descartar trabalho recente sem comparar o diff. Registrar a hipótese com observação que a falsifique.
2. **Matriz de superfícies:** cobrir todos os perfis e estados afetados; pensar também nos vizinhos que compartilham componentes, tipografia, CSS, APIs, estado, animação, autenticação, cache ou build. Testar normal, vazio, carregando, erro, offline, permissão negada, conteúdo longo, foco e retorno. Delimitar claramente as áreas preservadas pelo proprietário.
3. **Precisão visual e funcional:** verificar alinhamento, proporções, bordas, contraste, cores funcionais, espaçamento, densidade, opacidade, hierarquia, sombras, transparências, ícones, SVG/viewBox/aspect ratio, tipografia, feedback e acessibilidade. Não aceitar ícones distorcidos, botões soltos, sobreposição, clipping, texto ilegível, toque sem efeito ou componente sem ação real.
4. **Gestos e animações:** arrastar superfícies móveis pelo corpo/área de pega legítima, não obrigar o dedo a acertar só a seta. Testar pointerdown/move/up/cancel, touch e mouse, scroll interno, colisão com botões e mapa, limiares e cancelamento. Verificar se animação altera a posição do alvo e impede clique; limitar movimento decorativo persistente que prejudique hit-testing. Respeitar `prefers-reduced-motion`.
5. **Correção responsável:** alterar fonte canônica CSS/JS/HTML, componentes e gerador/consumidores/cache/build juntos. Evitar novos `!important`, regras finais para anular originais, cópias de arquivo e patches cumulativos; preservar estados, rotas, identidade de marca e segurança. Uma melhoria não pode ser só mais uma camada para disfarçar a falha.
6. **QA cruzado:** usar navegação e cliques reais de Chromium/Playwright com dimensões declaradas, screenshots e inspeção de DOM/caixas computadas/console quando pertinente. Regressão desktop e mobile para mudanças compartilhadas; testar Android/WebView e APK em aparelho quando forem requisitos. Teste unitário isolado ≠ aprovação de interface nem de APK. Não esconder falhas de baseline nem enfraquecer asserções.
7. **Handoff rastreável:** cada especialista informa arquivos, commit, passos realizados, ferramenta/teste e resultado, falhas não corrigidas e próxima ação. O Maestro distribui responsabilidades sem duplicar arquivos simultaneamente; QA/Corregedoria bloqueiam entrega sem evidência. Se modelo/API indisponível, prosseguir nas etapas determinísticas, salvar checkpoint e consultar May somente pelo protocolo permitido, sem alegar sucesso fictício.

## Ronda Pro / Grupo Maxvel: contrato de acabamento e revisão integral (P0)
- **Escopo:** revisão COMPLETA da qualidade de UI/UX, animações, ícones e interações em toda a aplicação e no site; correção funcional de falhas encontradas em qualquer perfil. **Reformulação visual prioritária somente em Vigia/Ronda e Vistoriador**, usados exclusivamente em celular. Preservar o visual comercial aceito de COI/Admin, realizando regressão desktop e correção pontual se houver bug.
- **Móvel, barras laterais:** painel que recolhe/abre ou desliza deve responder ao arrasto a partir de toda a superfície não interativa da barra, não só da seta. Não roubar rolagem, zoom/pan do mapa ou clique dos botões. Permitir fechar/reabrir e comportamento consistente em várias direções.
- **Barra superior Vigia/Vistoriador:** pode escurecer; aplicar quando for funcional um **gradiente vertical de opacidade**, mais escuro/opaco no topo, mas jamais 100% opaco, dissipando progressivamente até transparência total na base. Preservar leitura, acesso a botões, mapa claro, safe areas e gestos; não colocar camada com `pointer-events` que roube toque.
- **Controles translúcidos:** escolher a cor adequada a cada ação/estado e contraste, não apenas aumentar alpha global. Lanterna não deve ficar branca sem justificativa semântica: definir cor coerente com liga/desliga e contraste em tema grafite aprovado; preservar indicações de alerta/pânico e marca Maxvel.
- **Ícones em todos os perfis, incluindo website desktop:** corrigir distorção de SVG, imagens e botões na fonte: dimensões proporcionais, `viewBox`, `preserveAspectRatio`, `object-fit`, `flex-shrink`, height/width e alinhamento. Testar escalas, zoom 100/125/200%, densidade móvel e estados ativos/desativados.
- **Chat e notificações:** mensagem deve abrir em painel legível, dentro da viewport móvel, movimento de descida real, lista/histórico e compositor alcançáveis, sem ir à esquerda ou ficar cortada. Testar scroll, teclado virtual, badge, navegação e painel vazio. Comparar com PR #78 sem duplicar código.
- **Cobertura de campo:** mapa, ronda, vistoria, rotas próprias, cronômetro, posições/gestos, camera/evidências, lanterna, pânico, alerta, ocorrência, offline e mensagens; UI desktop dos demais perfis com checagem funcional de regressão. Dimensões mobile 320/360×800/390×844/393×852 e desktop 1440×960; navegador real, e Android real quando necessário.
- **Portão da entrega:** não considerar APK aprovado enquanto gesto, corte, ícone, painel ou testes de navegação essencial falharem. Não publicar correções não verificadas na `main` do produto sem autorização e QA. A segunda prioridade continua sendo Michelle, com estado intacto.

## Critérios específicos por especialidade
- Maestro/Project Studio/Architect: mapear todos os requisitos, dependências e critérios de aceite, distribuir sem duplicar edições.
- UI/UX Designer/UI Guardian/Frontend/Mobile Builder: detalhamento visual, gestos e medidas reais em mobile + desktop, implementação sem maquiagem.
- Backend/Database/Integration/Security/DevOps: entradas inválidas, contratos, idempotência, dados, falhas, observabilidade, permissões e recuperação verificáveis.
- QA/Corregedoria/Evolution/Skills/Persona: regressão independente, prevenção de reincidência, documentação do motivo e reprovação explícita de falsas conclusões.

**Meticulosidade é obrigatória durante o trabalho, mas não dá acesso novo a ferramentas, contas ou segredos.** Nunca afirmar que a habilidade foi seguida apenas porque o arquivo foi carregado.
