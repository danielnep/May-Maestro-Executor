# Chat Bridge Proof - Motor de Reservas Concorrente

Motor de reservas implementado estritamente com a biblioteca padrão Python (`sqlite3`, `threading`, `uuid`, `unittest`, `random`).

## Características e Invariantes do Domínio

1. **Capacidade Finita por Recurso**:
   - O recurso possui capacidade definida e campo `available` controlado atomicamente em transações SQLite (`BEGIN IMMEDIATE`).
   - Esgotamento de capacidade lança exceção imediata (`Capacity exhausted`) sem corromper o estoque.

2. **Idempotência por Locatário (`tenant_id`, `idempotency_key`)**:
   - Chave composta única por locatário e idempotency_key.
   - Repetições idênticas retornam o mesmo ID de reserva e status anterior sem debitar estoque adicional.

3. **Rejeição de Replay Conflitante**:
   - Se o mesmo `tenant_id` e `idempotency_key` forem reenviados com payload diferente (`payload_sig`), a operação é rejeitada com erro de conflito (`ValueError: Conflict: Idempotency key reused with different payload`).

4. **Cancelamento Seguro sem Duplicar Devolução**:
   - Cancelamento estorna 1 unidade de volta à disponibilidade do recurso.
   - Tentativas subsequentes de cancelamento da mesma reserva são idempotentes e NÃO incrementam a capacidade novamente.

5. **Concorrência e Disputa Multithread com Seed Fixo**:
   - Suporte a múltiplas threads simultâneas operando conexões dedicadas (`threading.local`) com controle transacional `BEGIN IMMEDIATE`.
   - Testes concorrentes usam `random.Random(seed=42)` garantindo reprodutibilidade pseudoaleatória e integridade rigorosa da conservação de estoque.

## Instruções de Execução dos Testes

Executar a suíte de testes unitários diretamente via stdlib:

```bash
python3 -m unittest discover -s apps/chat-bridge-proof -p "test_*.py"
```

ou especificamente:

```bash
python3 -m unittest apps/chat-bridge-proof/test_engine.py
```

## Limites Comprovados e Considerações

- **Concorrência SQLite**: Utiliza lock `BEGIN IMMEDIATE` para prevenir deadlocks de escrita em bancos SQLite sob contenção.
- **Escopo**: Focado em ambiente local e arquivo/memória SQLite; para banco de dados distribuído ou PostgreSQL, a sintaxe de transação deve ser ajustada para `FOR UPDATE` / advisory locks.
