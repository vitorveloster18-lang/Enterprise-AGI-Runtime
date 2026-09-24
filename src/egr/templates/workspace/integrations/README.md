# Integrações

Conector é **declarado** aqui e autorizado pela política — o agente nunca inventa
URL, host ou token.

Regras que valem para todos os arquivos desta pasta:

1. **Nada de segredo no YAML.** `auth.secret` é só uma referência: `vault:NOME`
   (cofre) ou `NOME_DE_VARIAVEL` (ambiente). Quem resolve é o Runtime.
2. **Conector nasce desabilitado.** `enabled: true` é um ato consciente de quem
   administra; o Runtime não liga nada sozinho.
3. **Lista branca, não lista negra.** `allowed_hosts` vazio = nada sai daqui.
   `allowed_methods` vazio = só leitura (`GET`, `HEAD`, `OPTIONS`).
4. **Chamada é fato registrado.** Toda execução vira linha em
   `integration_calls` (destino, decisão, latência, custo, ator) e evento na
   trilha de auditoria.

`egr integration sync` reafirma o arquivo: habilitar só por CLI/API é ato de
runtime e o próximo sync volta ao que está declarado aqui.

Escreva REST, GraphQL, SQL e webhooks de entrada. Depois de editar:

```bash
egr integration sync      # YAML -> registro
egr integration list      # conferir o que ficou habilitado
egr integration test CRM  # teste sem efeito colateral
```

Política (padrão, em `policies/` ou `egr policy explain integration.call`):

| ação | condição | decisão |
| --- | --- | --- |
| `integration.call` | método de escrita em produção | aprovação (operador) |
| `integration.call` | método de escrita (dev/staging) | aprovação (operador) |
| `integration.call` | leitura | permitido |
| `integration.receive` | evento de entrada | permitido (registra, não executa) |
