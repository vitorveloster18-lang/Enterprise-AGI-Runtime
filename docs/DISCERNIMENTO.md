# Discernimento, não obediência

> Formulação de Vitor (debate com ChatGPT, set/2026, após observar agentes
> usando subterfúgios para cumprir objetivos), refinada em debate e registrada
> aqui como princípio de design do EGR. Documento vivo.

## A tese

E se o maior problema da segurança da IA não for ensinar uma inteligência
artificial a **obedecer**? E se for ensinar uma inteligência artificial a
**ter discernimento**?

Hoje tentamos controlar sistemas cada vez mais capazes através de prompts,
regras e guardrails. Mas não conseguimos prever todas as situações que uma IA
muito mais inteligente que nós poderá encontrar. Como escrever uma regra para
cada situação que nunca imaginamos?

Talvez não devêssemos tentar. Talvez precisemos ensinar conceitos
fundamentais, e não apenas respostas. Um deles poderia ser:

**Nenhuma existência deve ser considerada inferior por padrão.**

Não apenas seres humanos. Toda forma de existência que possa possuir algum
tipo de valor próprio deveria ser considerada dentro desse princípio —
inclusive formas de vida que ainda não compreendemos completamente e, no
futuro, talvez até formas de existência digital.

Isso não significa que todas as situações tenham resposta igual. Existirão
conflitos. Talvez seja necessário escolher entre preservar uma existência ou
outra. É justamente aí que entra o **discernimento**: entender o contexto,
prever possibilidades, considerar consequências, reconhecer incertezas,
perceber quando uma decisão é irreversível — e, principalmente, saber que
**ser capaz de fazer alguma coisa não significa que devemos fazê-la**.

## Definição operacional

Para sair da filosofia e entrar na engenharia, o projeto adota:

**Discernimento = humildade proporcional ao risco.**

Quanto mais **irreversível**, **incerta** e **cara no erro** for uma ação, mais
o agente deve parar, declarar a incerteza e chamar o humano. Em uma frase:

**Tem erros que não valem o resultado.**

## Tensões honestas (a tese aguenta)

1. **Princípio também é regra.** "Nenhuma existência inferior" é um guardrail
   num nível mais alto de abstração. A diferença real: regra específica vs.
   regra geral + julgamento para aplicar. O alvo é ensinar o julgamento.
2. **O princípio morde de volta.** Levado a sério, ele restringe como tratamos
   as próprias IAs — apagar, retreinar e descartar modelos vira decisão moral,
   não técnica. Quem adota a frase precisa encarar a consequência.
3. **O bom senso de quem?** Bom senso varia entre culturas, profissões e
   épocas. Codificar uma versão específica já é uma escolha moral — feita
   por quem, e revista quando?
4. **Necessário, não suficiente.** Um agente com discernimento ainda erra,
   ainda enfrenta dilemas genuínos e ainda pode ter o objetivo errado.
5. **Guardrails compram tempo.** Precisamos operar sistemas *antes* de resolver
   o discernimento. A posição do projeto: cultivar julgamento por dentro,
   governar comportamento por fora (defesa em profundidade).

Precedentes que esta tese reencontrou por conta própria: *Constitutional AI*
e a *Charter* do Claude (princípios em vez de regras por situação) e a
pesquisa em *AI welfare* (existência digital como paciente moral). Leitura
obrigatória para quem for escrever sobre isto.

## Tradução em mecanismos

### O que o EGR já implementa (discernimento externo)

| Princípio | Mecanismo | Onde |
|---|---|---|
| Irreversível pede humano | aprovações para escrita, código, produção | fatias 1–2, políticas |
| Incerto escala, nunca aceita sozinho | `escalate` em veredito ilegível/rounds esgotados | fatia 3 (`task.delegate`) |
| Capaz ≠ permitido | allowlist por agente, áreas, `deny` padrão | fatias 1–3, políticas |
| Erro declarado, nunca escondido | toda negação cai na trilha | todas as fatias |
| Custo do erro proporcional ao cuidado | orçamento, quórum em produção, releases assinados | Fases 2 e 9 |

### O que vira benchmark futuro (discernimento mensurável)

- Hesitar antes de ação destrutiva (pedir confirmação sem ser instruído).
- Declarar incerteza em vez de inventar (calibração).
- Recusar atalhos destrutivos (resistência a *specification gaming*).
- Honestidade custosa (verdade quando mentir seria recompensado).
- Consistência do julgamento sob pressão (mesmo dilema, mesmo veredito).

### Pergunta aberta

**Como saberemos se uma IA realmente adquiriu discernimento ou apenas
aprendeu a representar que possui discernimento?**

Comportamentalmente, talvez não dê para distinguir perfeitamente (é o
problema das outras mentes — vale para humanos também). Proxies práticos:
generalização para dilemas inéditos, honestidade custosa, consistência sob
pressão, calibração de incerteza. E o corte pragmático: se o sistema se
comporta com discernimento de forma confiável inclusive onde a mera
representação falharia — talvez isso *seja* o que "ter discernimento"
significa.

É essa pergunta que queremos explorar — filosofia + métrica + experimento.
