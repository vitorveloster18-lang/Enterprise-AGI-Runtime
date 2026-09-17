# Integrações do workspace ACME

Conector é **declarado**: o agente não inventa URL, host nem token. A credencial
vive no cofre (`vault:NOME`) ou em variável de ambiente — nunca neste arquivo.

```bash
egr integration sync     # integrations/*.yaml -> registro
egr integration list     # o que existe e o que está habilitado
egr integration enable WAREHOUSE --by human:vitor
```

| conector | tipo | serve para | habilitado |
| --- | --- | --- | --- |
| `WAREHOUSE` | sql | leitura analítica no banco do workspace (sqlite) | não |
| `CRM` | rest | clientes e contratos (somente leitura) | não |
| `PEDIDOS` | rest | criação de pedidos (escrita → aprovação) | não |
| `ERP` | graphql | estoque e pedidos | não |
| `FORNECEDOR` | webhook | eventos de pedido (entrada) | não |
