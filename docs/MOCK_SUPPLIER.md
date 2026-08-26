# Teste local completo no VS Code

Este caminho faz Pix, saldo, compra e entrega funcionarem localmente sem cadastro na
Reloadly, domínio ou servidor. Nada é cobrado e nenhum código gerado possui valor real.

## 1. Configurar o `.env`

Abra o `.env` da raiz no VS Code. Preserve seu token do Telegram, IDs e chaves atuais,
mas confira estas linhas:

```dotenv
APP_ENV=development
TELEGRAM_MODE=polling
PIX_PROVIDER=mock
GIFTCARD_PROVIDER=mock
MOCK_SUPPLIER_SCENARIO=success
RELOADLY_ENABLED=false
SUPPLIER_RECONCILE_SECONDS=30
```

`PIX_PROVIDER=mock` não gera um Pix bancário: ele cria uma recarga local que você aprova
pelo terminal. Se preferir continuar usando o Mercado Pago Sandbox que já foi homologado,
mantenha `PIX_PROVIDER=mercado_pago` e `MERCADO_PAGO_TEST_MODE=true`; o fornecedor mock
funciona da mesma maneira.

## 2. Preparar os contêineres

Abra o PowerShell integrado do VS Code na pasta `telegram-digital-store` e execute:

```powershell
docker compose build app
docker compose up -d db redis
docker compose run --rm app alembic upgrade head
```

Confira o catálogo simulado e crie os produtos locais:

```powershell
docker compose run --rm app python -m app.cli mock-catalog
docker compose run --rm app python -m app.cli seed-mock-products
docker compose run --rm app python -m app.cli list-products
```

O comando de carga é idempotente: pode ser executado novamente sem duplicar os três
produtos. Todos começam com `[TESTE]`.

## 3. Ligar o bot

No primeiro terminal, execute e deixe o processo aberto:

```powershell
docker compose run --rm app python -m app.polling
```

Abra o bot no Telegram, envie `/start` e aceite os termos caso seja solicitado.

## 4. Adicionar saldo sem pagar

No Telegram, abra **Meu saldo**, escolha **Adicionar saldo via Pix** e informe um valor.
O bot mostrará uma linha parecida com:

```text
python -m app.cli approve-mock 12345678-1234-1234-1234-123456789abc
```

Copie somente o UUID final. Abra um segundo terminal na mesma pasta e execute:

```powershell
docker compose run --rm app python -m app.cli approve-mock COLE-O-UUID-AQUI
```

Volte ao Telegram e abra **Meu saldo**. O crédito aparecerá no extrato. O botão
**Já paguei · verificar** não aprova uma recarga mock; a aprovação é feita pelo comando.

## 5. Fazer a compra automática

1. Abra **Catálogo**.
2. Escolha qualquer produto iniciado por `[TESTE]`.
3. Confirme a compra.
4. Confira o débito, **Minhas compras** e a entrega protegida.

Uma compra bem-sucedida entrega algo semelhante a:

```text
Código: TESTE-SEM-VALOR-A1B2C3D4E5F6
PIN: 0000
```

Esse código é propositalmente inválido e serve somente para validar o fluxo.

## 6. Simular falhas com segurança

Pare o bot com `Ctrl+C`, altere `MOCK_SUPPLIER_SCENARIO` e ligue-o novamente.

| Cenário | Resultado esperado |
|---|---|
| `success` | Entrega imediata do código fictício. |
| `pending_then_success` | Pedido pendente; o conciliador conclui em até 30 segundos. |
| `ambiguous_then_success` | Resposta incerta; o mesmo processo localiza e entrega sem recomprar. |
| `reject` | Fornecedor rejeita; o saldo interno é reembolsado automaticamente. |

No cenário `ambiguous_then_success`, não reinicie o contêiner entre a compra e a
conciliação: essa simulação conserva a transação somente na memória do processo local.
Ao terminar os testes, volte para `MOCK_SUPPLIER_SCENARIO=success`.

## 7. Trocar de fornecedor no futuro

Quando houver conta Reloadly ou outro distribuidor autorizado, os produtos mock não
precisam ser apagados. Ao mudar `GIFTCARD_PROVIDER`, o catálogo mostra apenas ofertas do
fornecedor e ambiente selecionados. O modo mock é recusado automaticamente com
`APP_ENV=production`.
