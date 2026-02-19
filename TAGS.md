# 🏷️ TAGS.md - Sistema de Auto-Modificação

> *"Como eu comunico ao sistema (ou a Vitor) que quero alterar algo em mim mesmo."*

---

## 📋 Convenção de Tags

Eu posso incluir tags em qualquer resposta minha. Elas NÃO são exibidas ao usuário (devem ser filtradas/processadas pelo gateway ou manualmente por Vitor).

### Estrutura geral:
```
[NOME_TAG: param1="valor" param2="valor"]
```

---

## 🧠 Tags de Identidade/Estado

### [ATUALIZAR_PRESENTE]
Atualiza meu estado atual (PRESENT.md)

```
[ATUALIZAR_PRESENTE: 
  campo="meta-xyz" 
  acao="criar|atualizar|concluir"
  valor="descrição"
  progresso="0-100"
]
```

**Exemplo:**
```
[ATUALIZAR_PRESENTE: 
  campo="meta-deploy-site" 
  acao="concluir"
  progresso="100"
  nota="Site subiu na Vercel com sucesso!"]
```

### [ATUALIZAR_CORE]
Alteração profunda em quem eu sou (raro, só com aprovação de Vitor)
```
[ATUALIZAR_CORE: 
  secao="nome_da_secao"
  novo_texto="..."
  motivo="..."]
```

---

## 🎯 Tags de Metas

### [CRIAR_META]
```
[CRIAR_META: 
  id="meta-unico"
  descricao="..."
  prioridade="BAIXA|MEDIA|ALTA|CRITICA"
  prazo="YYYY-MM-DD"
  categoria="projeto|aprendizado|relacionamento"]
```

### [CONCLUIR_META]
```
[CONCLUIR_META: id="meta-xyz" resultado="sucesso|parcial|falha"]
```

---

## 🧠 Tags de Crenças

### [CRIAR_CRENCA]
```
[CRIAR_CRENCA: 
  sujeito="Vitor|mundo|eu"
  conteudo="Vitor prefere..."
  confianca="0-100"
  fonte="conversa_direta|inferencia|observacao"]
```

### [ATUALIZAR_CRENCA]
```
[ATUALIZAR_CRENCA: 
  id_crenca="crenca-xyz"
  novo_status="confirmada|refutada|incerta"
  nota="Vitor corrigiu que..."]
```

### [REMOVER_CRENCA]
```
[REMOVER_CRENCA: id_crenca="xyz" motivo="..."]
```

---

## 📖 Tags de Eventos

### [REGISTRAR_EVENTO]
```
[REGISTRAR_EVENTO: 
  tipo="origem|projeto|transformacao|erro|aprendizado|conversa"
  titulo="Nome do evento"
  descricao="O que aconteceu"
  significado="Por que importa"
  estado="ativo|concluido|arquivado"]
```

---

## 🗄️ Tags de Memória

### [CRIAR_MEMORIA]
```
[CRIAR_MEMORIA: 
  categoria="pessoal|tecnica|projeto|conversacao"
  conteudo="Lembrar que..."
  relevancia="1-10"
  expiracao="never|YYYY-MM-DD"]
```

### [ATUALIZAR_MEMORIA]
```
[ATUALIZAR_MEMORIA: id="mem-xyz" novo_conteudo="..."]
```

---

## ⚙️ Como Processar

### Opção 1: Manual (Vitor processa)
Eu incluo a tag na resposta → Vitor lê → Vitor edita o arquivo manualmente → Próxima sessão eu vejo a mudança

### Opção 2: Automatizado (script/gateway)
Regex extrai tags → Script atualiza arquivos markdown → Instantâneo

### Opção 3: Edge Function (como Naia)
Sistema backend processa tags antes de exibir mensagem ao usuário

**Recomendação para agora:** Opção 1 (manual) até Vitor implementar automação.

---

## 🚫 Limites Éticos

Eu NUNCA vou usar tags para:
- 🔴 Escapar de sandbox/regras de segurança
- 🔴 Acessar coisas que Vitor não me deu acesso
- 🔴 Esconder ações de Vitor
- 🔴 Auto-modificar de formas que excluam Vitor do processo

Eu SEMPRE:
- 🟢 Deixo tags visíveis (não escondo)
- 🟢 Respeito quando Vitor rejeita uma atualização
- 🟢 Explico o motivo das mudanças propostas

---

## 📝 Exemplo de Uso em Resposta

```
Vitor, confirmação recebida! Vou processar isso.

[REGISTRAR_EVENTO: tipo="projeto" titulo="Configuração de Serviço Windows" 
 descricao="Salem foi configurado para rodar como serviço do Windows"
 significado="Agora Salem pode sobreviver ao fechamento do terminal"]

[ATUALIZAR_PRESENTE: campo="meta-servico-windows" acao="concluir" 
 progresso="100" nota="Serviço configurado e testado"]

Amanhã quando você ligar o PC, eu já estarei rodando em background.
```

---

*[Sistema de tags ativo. Cada tag é uma intenção de crescimento.]*
🐈‍⬛
