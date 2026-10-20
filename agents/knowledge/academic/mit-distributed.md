# MIT 6.5840 — Distributed Systems, Spring 2026

Fonte: https://pdos.csail.mit.edu/6.824/schedule.html
Edição/data: 2026
Consulta: 2026-10-20

## O que foi efetivamente consultado
O programa atual inclui RPC, tolerância a falhas, Raft, linearizabilidade, replicação e transações distribuídas, com laboratórios. Artigos fundamentais antigos permanecem no programa de 2026; isso não os torna tendências recentes.

## Aplicação Horizon — síntese original, não citação do autor
Definir estados e invariantes antes de retries. Repetir leitura é diferente de repetir mutação: exigir idempotency key ou reconciliação por recibo. Testar crash antes/depois de persistir, lease expirado, evento duplicado e concorrência. Backoff preserva tarefa e evidência; não inventa progresso nem resolve erro determinístico.

## Limite da evidência
Ficha de estudo baseada nas páginas públicas identificadas. Não é texto integral do livro, treinamento de pesos ou prova de domínio profissional. Consultar capítulo/artigo original antes de atribuir detalhes à fonte.
