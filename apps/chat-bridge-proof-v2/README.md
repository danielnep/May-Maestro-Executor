# Aplicativo Sintético e Testável de Reservas (Python stdlib)

Este diretório (`apps/chat-bridge-proof-v2/`) contém uma implementação robusta de motor de reservas transacional utilizando exclusivamente a biblioteca padrão Python (`sqlite3`, `threading`, etc.).

## Funcionalidades
- **Capacidade Finita por Recurso**: Limite estrito de vagas.
- **Isolamento por Cliente/Locatário**: Dados rigorosamente separados por `tenant_id`.
- **Chaves Idempotentes**: Repetições com a mesma chave retornam o estado anterior sem novos efeitos ou custos.
- **Conflito de Replay**: Payload conflitante com chave idempotente existente falha explicitamente.
- **Cancelamento Repetido Seguro**: Cancelar músltiplas vezes não devolve capacidade mais de uma vez.
- **Concorrência**: Proteção transacional com `BEGIN IMMEDIATE` para disputas pela última vaga.

## Executando os Testes
```bash
python3 -m unittest apps/chat-bridge-proof-v2/tests/test_engine.py
```
