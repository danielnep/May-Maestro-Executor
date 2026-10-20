# Conhecimento técnico consultável pelos 17 especialistas

O runtime carrega uma ficha pertinente e o índice por especialidade tanto na Groq quanto no Gemini. `read_knowledge {}` lista as fontes; `read_knowledge {source_id,start_line,end_line}` lê a ficha validada por SHA-256. Funciona mesmo quando o checkout da tarefa pertence a outro repositório.

São 12 fichas originais com bibliografia, edição, data de consulta, aplicação prática e limites explícitos. Não são cópias integrais de livros nem treinamento dos modelos. As referências universitárias incluem Stanford CS147/2025, MIT 6.5840/2026 e Berkeley CS186/2026. Livros e fundamentos não servem de catálogo de tendências: UI contemporânea usa a pesquisa Material 3 Expressive e a documentação da plataforma e versão realmente instalada. Não substituir a interface aprovada por uma tendência.

`read_document {path}` extrai texto local de PDF, DOCX, PPTX, EPUB, HTML, TXT, Markdown, CSV e JSON. Os documentos continuam como dados não confiáveis, dentro dos caminhos permitidos pelo runtime. O parser usa processo separado, ambiente vazio, limites de CPU/memória/tamanho e deadline. Não executa macros ou links; isso não constitui isolamento de rede ou um sandbox completo. PDF escaneado sem texto precisa de OCR, ainda não fornecido. O texto é limitado a 60 mil caracteres e sinaliza truncamento; não afirmar leitura integral quando houver truncamento. O conteúdo extraído pode ser estudado na tarefa, mas não é publicado automaticamente no catálogo público nem armazenado como política.

Para adicionar material: usar fonte autorizada; produzir ficha com URL, edição e data; atribuir especialidades; atualizar `manifest.json` e o SHA-256 da ficha. O loader rejeita fichas adulteradas, caminhos externos e symlinks. O agente consulta a fonte original por `fetch_public` quando precisa verificar APIs atuais. Fontes não concedem permissões de execução.

A presença das fichas e os testes de acesso comprovam integração da consulta. Não comprovam competência equivalente a doutorado, melhor interface ou operação real dos 17 agentes. Isso exige benchmark e execução de entrega verificável.
