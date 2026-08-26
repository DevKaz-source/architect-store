# Checklist de implantação

## 1. Infraestrutura

- Use domínio próprio com HTTPS válido e proxy reverso confiável.
- Não exponha a porta do PostgreSQL à internet.
- Não exponha o Redis à internet; use rede privada e `REDIS_URL` interno.
- Use senha exclusiva e forte para o banco.
- Mantenha `APP_ENV=production` e `TELEGRAM_MODE=webhook`.
- Restrinja o acesso SSH e aplique atualizações do sistema.
- Configure reinício automático, health checks e alertas de erro.

## 2. Segredos

- Gere `TELEGRAM_WEBHOOK_SECRET` e `DATA_ENCRYPTION_KEY` com
  `python scripts/generate_secrets.py`.
- Nunca coloque `.env`, tokens, chaves ou arquivos de estoque no Git.
- Guarde uma cópia segura da `DATA_ENCRYPTION_KEY`. Sem ela, entregas e chamados antigos
  não podem ser recuperados.
- Rotacione o token do bot e as chaves de webhook após qualquer suspeita de vazamento.
- Não reutilize a chave secreta do webhook como Access Token.

## 3. Banco e backup

- Execute `alembic upgrade head` antes de iniciar a nova versão.
- Faça backup criptografado diário do PostgreSQL.
- Teste a restauração em ambiente isolado pelo menos uma vez por mês.
- Monitore divergências entre `wallets.balance_cents` e a soma de `wallet_entries`.
- Retenha logs de auditoria sem incluir entregas, senhas ou códigos Pix completos.
- Comece com uma única réplica do serviço `app`. O conciliador é idempotente, mas duas
  réplicas podem enviar avisos duplicados até existir uma fila distribuída dedicada.

## 4. Mercado Pago

- Use a aplicação de Checkout Transparente com API de Orders.
- Cadastre uma chave Pix na conta vendedora.
- Primeiro use credenciais de teste e os cenários oficiais de Pix.
- Configure o evento **Order (Mercado Pago)** na URL
  `https://SEU-DOMINIO/webhooks/mercado-pago`.
- Simule o webhook no painel e confirme respostas HTTP 200.
- Valide criação, pagamento, expiração, reenvio do webhook, reembolso e contestação.
- Faça a virada para o Access Token de produção somente depois da homologação.

## 5. Telegram

- Deixe o bot acessível somente em conversas privadas para compras.
- Confirme o header secreto do webhook; o projeto já rejeita chamadas sem ele.
- Use um grupo privado separado para suporte, se configurar `SUPPORT_CHAT_ID`.
- Os administradores devem ativar verificação em duas etapas em suas contas Telegram.
- Teste entrega, clique duplicado, reinício durante uma compra e histórico do pedido.

## 6. Reloadly

- Confirme `GIFTCARD_PROVIDER=reloadly`; o fornecedor mock é bloqueado em produção.
- Faça o primeiro deploy com `RELOADLY_ENVIRONMENT=sandbox` e
  `RELOADLY_LIVE_ENABLED=false`.
- Rode `reloadly-balance`, `reloadly-sync` e uma compra completa depois do deploy.
- Monitore `list-review-supplier-orders`; um pedido incerto nunca deve ser repetido à mão.
- Antes de Live, siga integralmente [RELOADLY.md](RELOADLY.md).

## 7. Antes da abertura

- Cadastre termos, privacidade, política de reembolso, identificação e contato da empresa.
- Importe apenas estoque cuja revenda e forma de entrega estejam autorizadas.
- Teste cada produto com uma unidade real.
- Defina prazo e escala de suporte.
- Faça uma recarga Pix de valor baixo e confira a conciliação bancária.
- Mantenha um botão operacional para desativar produtos sem apagar o histórico.
