# Reloadly na Architect Store

Este guia começa em Sandbox e termina na decisão controlada de ativar dinheiro real.

## 1. Obter credenciais de teste

1. Crie ou acesse sua conta no painel da Reloadly.
2. Ative o modo **Test/Sandbox** no seletor do painel.
3. Abra **Developers / API settings**.
4. Copie o `client_id` e o `client_secret` do ambiente de teste.
5. Guarde as chaves apenas no `.env` local e, depois, no gerenciador de variáveis da
   hospedagem. Nunca commite o `.env`.

O bot solicita um token OAuth específico para
`https://giftcards-sandbox.reloadly.com`. Credenciais Live não são intercambiáveis com
as de Sandbox.

## 2. Configurar no Windows e VS Code

No arquivo `.env` da raiz:

```dotenv
GIFTCARD_PROVIDER=reloadly
RELOADLY_ENABLED=true
RELOADLY_ENVIRONMENT=sandbox
RELOADLY_CLIENT_ID=cole_o_client_id_de_teste
RELOADLY_CLIENT_SECRET=cole_o_client_secret_de_teste
RELOADLY_SENDER_NAME=Architect Store
RELOADLY_LIVE_ENABLED=false
RELOADLY_TIMEOUT_SECONDS=20
SUPPLIER_RECONCILE_SECONDS=60
MIN_SUPPLIER_GROSS_MARGIN_BPS=500
```

Abra o terminal PowerShell do VS Code na pasta `telegram-digital-store`:

```powershell
docker compose build app
docker compose run --rm app alembic upgrade head
docker compose run --rm app python -m app.cli reloadly-balance
```

O último comando deve mostrar `Reloadly sandbox` e a moeda/saldo da carteira de teste.

## 3. Sincronizar e pesquisar catálogo

```powershell
docker compose run --rm app python -m app.cli reloadly-sync --country BR
docker compose run --rm app python -m app.cli reloadly-catalog --country BR --search "Play"
```

Anote o `id`, a moeda e uma denominação disponível. O fluxo automático desta versão só
aceita custo de fornecedor em BRL e uma denominação cujo custo fixo possa ser confirmado.
Produtos em outra moeda ou sem mapa de custo são recusados até existir conversão cambial
auditável. Confira também as regras regionais e de resgate antes de publicar.

## 4. Cadastrar uma oferta sem colocá-la à venda

```powershell
docker compose run --rm app python -m app.cli add-reloadly-product `
  --slug google-play-50 `
  --product-id 1234 `
  --country BR `
  --denomination 50 `
  --sale-price "57,90" `
  --name "Google Play R$ 50"
```

Substitua `1234` pelo ID realmente exibido no seu catálogo. O comando valida a
denominação novamente na API e recusa produtos que exigem um `userId` adicional. O item
nasce pausado. Ative somente após conferir marca, região, validade, custo e margem:

```powershell
docker compose run --rm app python -m app.cli set-product-active --product google-play-50 --state on
```

O custo exibido é uma estimativa conservadora do catálogo (conversão mais taxas, sem
contar descontos promocionais). A resposta do pedido registra o valor efetivamente
cobrado pela Reloadly. Mantenha margem para taxas, tributos, suporte, chargebacks do Pix e
promoções. O bot consulta o produto novamente antes da compra e pausa a oferta se não
conseguir confirmar o custo em BRL ou se a margem bruta `(venda - custo) / venda` cair
abaixo de `MIN_SUPPLIER_GROSS_MARGIN_BPS` (`500` significa 5%).

## 5. Testar compra completa

1. Inicie o bot em polling.
2. Adicione saldo de teste ao usuário.
3. Abra o catálogo e compre o produto Reloadly.
4. Confirme no Telegram: pedido, débito, código/PIN/link, histórico e saldo.
5. Confirme a mesma transação em **Reports / Giftcards** no painel Reloadly de teste.

Fluxo de segurança:

- Rejeição confirmada antes de criar transação: reembolso automático do saldo interno.
- `SUCCESSFUL`: código consultado, criptografado e entregue no Telegram.
- `PENDING`/`PROCESSING`: conciliação automática, sem repetir a compra.
- Timeout/HTTP 5xx: estado `review`; o bot pesquisa pelo `customIdentifier` único.
- `FAILED`: revisão manual, pois o fornecedor pode ainda não ter devolvido os fundos.
- `REFUNDED`: reembolso interno idempotente.

Para revisar manualmente:

```powershell
docker compose run --rm app python -m app.cli list-review-supplier-orders
docker compose run --rm app python -m app.cli reconcile-supplier-order --order PED-XXXXXXXX
```

## 6. Checklist antes de Live

- Conta Reloadly verificada e autorizada para seu modelo comercial.
- Contrato/termos permitem revenda de cada marca e região publicada.
- Pelo menos uma compra Sandbox aprovada para cada tipo de produto.
- Margem calculada com custo real, taxa, câmbio, tributos e perdas.
- Saldo Live pequeno para o primeiro teste.
- Servidor online com PostgreSQL, Redis, backup e alertas.
- Termos, privacidade, política de reembolso e canal de suporte publicados.
- Pix de produção homologado e webhooks assinados funcionando.

Só depois troque conscientemente:

```dotenv
APP_ENV=production
GIFTCARD_PROVIDER=reloadly
RELOADLY_ENVIRONMENT=production
RELOADLY_CLIENT_ID=client_id_live
RELOADLY_CLIENT_SECRET=client_secret_live
RELOADLY_LIVE_ENABLED=true
```

`RELOADLY_LIVE_ENABLED` é uma trava extra. O aplicativo se recusa a iniciar Reloadly Live
fora de `APP_ENV=production` ou enquanto essa confirmação estiver `false`.

## Referências oficiais

- [API Reference de Gift Cards](https://docs.reloadly.com/gift-cards)
- [Ambiente Sandbox](https://developers.reloadly.com/developer-tools/sandbox)
- [Pedidos de gift cards](https://developers.reloadly.com/gift-cards/order-giftcard)
