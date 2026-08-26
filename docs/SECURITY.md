# Modelo de segurança

## Invariantes implementados

- Um depósito só gera um lançamento positivo com a chave `deposit:<uuid>`.
- O webhook é validado por HMAC-SHA256 em tempo constante.
- Depois do webhook, a API consulta a Order diretamente no Mercado Pago.
- Referência externa, identificador e valor precisam coincidir com o depósito local.
- Uma compra trava usuário, carteira e unidade de estoque no PostgreSQL.
- O callback de confirmação tem chave de idempotência; cliques repetidos devolvem o mesmo
  pedido.
- Cada compra Reloadly usa um `customIdentifier` único. Timeout, HTTP 5xx ou resposta
  divergente entram em `review` e nunca provocam uma segunda chamada de compra.
- Códigos obtidos na API do fornecedor são criptografados antes de serem salvos.
- A ativação Live exige `APP_ENV=production` e a trava explícita
  `RELOADLY_LIVE_ENABLED=true`.
- `PIX_PROVIDER=mock` e `GIFTCARD_PROVIDER=mock` são recusados em produção.
- Estoque e conteúdo de chamados ficam cifrados no banco.
- Administradores são autorizados por Telegram ID numérico, não por username.

## Ameaças ainda dependentes da operação

- Conta Telegram de administrador comprometida.
- Servidor, `.env` ou chave de criptografia comprometidos.
- Fornecedor entregando códigos inválidos ou revendidos.
- Uso indevido de uma licença legítima fora dos limites contratuais.
- Abuso de reembolso após revelação de código.
- Ausência de backup ou perda da chave de criptografia.

## Recomendações

- Use 2FA no Telegram, no provedor Pix e na hospedagem.
- Restrinja administradores ao mínimo necessário.
- Use cofre de segredos da hospedagem e rotação periódica.
- Não registre bodies de webhooks, QR Codes ou payloads de estoque em logs.
- Aplique limites por usuário e IP no proxy reverso.
- Faça revisão manual de valores altos e de divergências marcadas como `review`.
- Execute testes de restauração e resposta a incidente.
