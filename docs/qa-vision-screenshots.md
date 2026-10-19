# Auditoria visual dos especialistas Horizon

## O que cada peça realmente faz

1. **Playwright/Chromium** interage com a página e mede DOM, `getBoundingClientRect`, scroll, hit targets, visibilidade, console e estados. O browser check captura `mobile-rendered.png` e `desktop-rendered.png` em `runtime/web-evidence/<id>/`, com SHA-256 conferido. Um teste genérico em página sintética não é prova do aplicativo Ronda Pro.
2. **Visão multimodal (Gemini)** lê de fato os *bytes PNG*, via `inlineData` de `generateContent`, quando o agente autorizado escolhe `inspect_image {"index":0|1,"question":"..."}` depois de `run_check {"name":"browser-playwright"}` no mesmo ciclo. Index 0/1 só é válido se o manifesto contiver esses arquivos nessa ordem. Não existe leitura de screenshot apenas por mencionar seu nome no prompt. O resultado deve dizer `actual_image_provided: true`.
3. **OCR** é opcional para copiar texto visível, conferir grafias/labels quando não houver DOM acessível e identificar caracteres truncados. Não avalia alinhamento, gestos, ícones distorcidos, contraste, transparência ou acabamento. Não instalar OCR por padrão só para validar UI HTML, pois o DOM já contém o texto com maior precisão.
4. **Revisão da May** pode avaliar imagens reais enviadas nesta conversa ou em localização publicamente acessível devidamente autorizada. A consulta `MAY_CONSULT_REQUEST_V2` em GitHub Issue transporta **texto público e ações JSON**, não transfere automaticamente PNG para o ChatGPT. Não publicar imagens de clientes, credenciais ou sessões autenticadas em issues ou branches públicas. Capturas publicáveis são *opt-in* por `HORIZON_VISUAL_ARTIFACTS_PUBLIC_SAFE=true` apenas se aprovadas para distribuição pública; isso não cria uma API de visão do ChatGPT.

## Configuração atual do executor (GitHub Actions)
- `GEMINI_API_KEY`: secret existente e válido, nunca no commit.
- `GEMINI_MODEL=gemini-3.6-flash`: análise visual primária pelo mesmo endpoint do modelo.
- `GEMINI_FREE_TIER_CONFIRMED=true`: declaração explícita do operador; deve refletir plano/cota de fato, não suposição de que um modelo é grátis.
- `GEMINI_FREE_FALLBACK_MODEL=gemini-3.5-flash-lite`: **somente** segunda rota multimodal após HTTP **429 quota** do Gemini primário, com free-tier explicitamente confirmado; nunca após 401/403, payload inválido ou erro geral. Uma segunda falha preserva classificação sanitizada da indisponibilidade.
- DeepSeek do executor recebe texto, não screenshot. Um erro de visão não deve derrubar um especialista que ainda pode executar testes determinísticos.

## Diagnóstico quando ninguém consegue ler o print

- Sem PNG/index atual: `Tools.evidence_image` retorna erro de evidência ausente; executar browser check novamente e conferir `screenshots`, hashes, imagem e índice. Fotos de execução anterior podem não estar no próximo runner: não supor persistência entre jobs.
- `401/403`: conferir credencial/permissão do provedor, **não** tentar fallback gratuito ocultando autenticação.
- `429`: quota; fallback gratuito se habilitado e ainda disponível, senão checkpoint sem aprovação visual.
- `503`: erro temporário de servidor; reter evidência, recuperação controlada depois; não afirmar análise nem enviar captura a outro destino inesperado.
- `invalid_response`: estrutura de resposta do modelo inválida. Manter fato `actual_image_analyzed=false` e corrigir contrato/saída antes de considerar resultado.
- `VisionUnavailable` na tool: o especialista segue usando medidas determinísticas, mas não pode afirmar que viu a captura.

## Aceite P0 Ronda Pro

Vigia/Ronda, Vistoriador e site/COI/Admin devem ter perfis/estados testados em viewport real, incluindo arrastos por toda superfície das barras, foco/teclado, ícones sem deformação, animações sem instabilidade de clique, chat visível e controles com cores corretas. **Uma captura aprovada não prova gesto nem APK Android**. Exigir capturas e provas das ações após cada reparo, e inspeção visual real quando a composição exigir julgamento.
