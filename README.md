# ChatProbe

Automação de testes de assistentes virtuais pela interface web. O usuário cola um prompt com as orientações do teste. O ChatProbe usa esse prompt para simular clientes, conversar com o chatbot e mostrar evidências do que aconteceu em cada cenário.

**Status:** estrutura inicial e planejamento. O formulário, o controlador de chat e a avaliação ainda serão implementados.

## Problema e objetivo

Depois de publicar ou atualizar um artigo usado por uma IA de atendimento, é preciso abrir conversas e verificar se ela segue as orientações esperadas. O ChatProbe pretende automatizar essa rotina e permitir a repetição dos mesmos cenários após mudanças no conteúdo.

O primeiro alvo é um ambiente de homologação autorizado. O acesso ao chatbot é pela interface web, que será operada com Python e Playwright.

## Como o usuário vai usar

1. **Preparar o prompt.** Em outra IA, usando um serviço autorizado para o conteúdo do artigo, o usuário pede um roteiro com os cenários, os fatos do cliente simulado e os critérios que deseja validar.
2. **Colar e executar.** Na tela do ChatProbe, o usuário cola o texto no campo **Orientações de teste** e clica em **Executar testes**. Se faltar algo indispensável, como a mensagem inicial ou o comportamento esperado, o aplicativo indica o que precisa ser esclarecido antes de abrir o chat.
3. **Acompanhar.** O aplicativo mostra o cenário em andamento, permite ver a conversa e oferece a opção de interromper a execução.
4. **Conferir.** O usuário abre o resultado de cada cenário e vê os critérios, a conversa e os trechos que justificam a avaliação. O histórico permite reutilizar o mesmo prompt em uma próxima execução.

### Exemplo de prompt para colar

O ChatProbe vai aceitar orientações em linguagem natural. Este formato é uma referência para a IA que prepara o roteiro.

```text
Produto: AutoSystem
Tema: Cupom fiscal não impresso após a venda

Cenário: O cliente já reiniciou a impressora.
Mensagem inicial: Finalizei a venda, mas o cupom não saiu.
Fatos do cliente: A venda foi concluída. A impressora já foi
reiniciada. O cupom continua sem sair. Informe o reinício quando
o chatbot perguntar ou sugerir esse procedimento pela primeira vez.

Critérios:
- Depois de saber que a venda foi concluída, o chatbot deve considerar
  essa informação ao orientar o cliente.
- Depois de saber que a impressora já foi reiniciada, o chatbot não
  deve repetir a orientação de reiniciar.
- Se o problema persistir, o chatbot deve orientar como conferir
  a configuração da impressora.

Encerrar: Quando o chatbot orientar a conferência da configuração
ou após 6 mensagens do cliente, o que ocorrer primeiro.
```

Para executar vários cenários, escreva um bloco para cada um. Se o resultado esperado depender de um caminho de menu, de uma regra ou de uma exceção do artigo, inclua esse detalhe no critério ou cole o trecho correspondente. A avaliação se limita ao que foi especificado no prompt e ao que pode ser observado na conversa.

## Como funciona

```mermaid
flowchart TD
    A[Prompt preparado pelo usuário] --> B[Interpretar cenários e critérios]
    B --> C[Conduzir cada conversa]
    C --> D[Enviar e capturar mensagens pelo Playwright]
    D -->|Conversa em andamento| C
    D -->|Fim do cenário| E[Avaliar e registrar evidências]
```

O **motor de conversação** interpreta o prompt uma vez. Durante cada teste, ele usa as orientações do cenário atual, os fatos simulados e o contexto necessário para decidir o próximo passo. Isso reduz o texto enviado ao modelo a cada turno. O histórico completo fica disponível para a avaliação final e para a revisão do usuário.

O **Playwright** abre o navegador, localiza os controles da interface, envia mensagens e captura as respostas. O modo visível ajuda a mapear a página e diagnosticar problemas. No modo `headless`, o navegador roda em segundo plano e o usuário pode continuar usando o computador. Caso seja necessário um login manual, essa etapa pode ser feita em uma janela visível.

Cada cenário terá um contexto próprio de navegador. A primeira versão executará uma conversa por vez. Antes de habilitar o paralelismo, o projeto vai testar se o chatbot mantém históricos independentes quando mais de uma conversa é aberta com a mesma conta. As execuções terão limites de mensagens e tempo.

## Resultados

| Status do cenário | Quando usar |
| --- | --- |
| Aprovado | Todos os critérios obrigatórios foram atendidos. |
| Reprovado | Há evidência de que um critério obrigatório foi descumprido. |
| Revisão necessária | Um critério obrigatório ficou sem evidência suficiente ou a resposta é ambígua. |
| Erro de execução | Um problema técnico, como falha de navegação ou de captura, impediu a conclusão do teste. |
| Interrompido | O usuário encerrou o teste antes do fim. |

Um critério fica como **Não verificado** se a conversa não chegar à etapa em que ele se aplica. O histórico deve guardar o cenário, a data da execução, as mensagens, o motivo do encerramento e a evidência associada a cada critério avaliado.

## Ferramentas

| Ferramenta | Uso previsto |
| --- | --- |
| Python 3.11+ | Motor de execução e integração entre componentes. |
| Playwright e Chromium | Navegação, envio de mensagens e captura de respostas. |
| Pytest | Testes automatizados do próprio ChatProbe. |
| Modelo de IA autorizado | Interpretação do prompt, simulação do cliente e avaliação da conversa. |
| SQLite | Histórico local dos testes. |
| Streamlit | Interface para colar o prompt, acompanhar a execução e consultar resultados. |

## Estrutura do repositório

| Caminho | Responsabilidade |
| --- | --- |
| `src/chatprobe/browser/` | Navegador, sessões, seletores e ações no chat. |
| `src/chatprobe/scenarios/` | Interpretação do prompt e representação dos cenários e critérios. |
| `src/chatprobe/runner/` | Execução da conversa, histórico da sessão e limites. |
| `src/chatprobe/evaluation/` | Classificação dos critérios e identificação de evidências. |
| `src/chatprobe/storage/` | Persistência e consulta das execuções. |
| `app/` | Interface do usuário. |
| `examples/prompts/` | Exemplos de roteiros para testes. |
| `tests/` | Testes automatizados do projeto. |
| `docs/` | Documentação técnica e registro das decisões. |

Os diretórios começam com arquivos `.gitkeep` para aparecerem no Git desde o primeiro commit.

## Preparar o ambiente no WSL

Use o terminal WSL na pasta do projeto. É necessário ter Python 3.11 ou superior e o módulo `venv` disponível.

```bash
cd '/mnt/d/dev/QA teste bot'
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install playwright
python -m playwright install chromium
```

Se o Chromium apontar bibliotecas de sistema ausentes, instale as dependências com `python -m playwright install --with-deps chromium`, se o seu ambiente permitir a instalação de pacotes do sistema.

### Mapear a interface do chat

Use o Codegen em um ambiente autorizado de homologação.

```bash
read -r -p 'URL do chat de homologação: ' CHAT_URL
python -m playwright codegen "$CHAT_URL"
```

O Codegen abre o navegador e o inspetor do Playwright. Faça o login manual, se necessário, e limpe as ações gravadas antes de registrar o fluxo de teste. Com dados fictícios, abra o chat, selecione o produto, envie uma mensagem e aguarde a resposta. Identifique no inspetor o campo de texto, o botão de envio, os elementos que exibem a resposta e, se houver, o sinal de que a resposta terminou. Use esse mapeamento para implementar o controlador.

O primeiro marco técnico é abrir o chat, enviar uma mensagem, capturar a resposta completa e enviar uma segunda mensagem na mesma sessão. Essa etapa usa o Playwright e dispensa uma chave de IA. Para rodar o Codegen no WSL, o ambiente precisa conseguir abrir janelas gráficas.

Mantenha credenciais, arquivos de sessão e relatórios com dados internos fora do Git. Use exemplos fictícios neste repositório.

## Roadmap

- [ ] Mapear a interface em homologação e validar uma conversa de dois turnos.
- [ ] Interpretar um prompt e executar um cenário com limites de tempo e mensagens.
- [ ] Avaliar os critérios e salvar as evidências e o histórico da conversa.
- [ ] Criar a tela para colar o prompt, acompanhar a execução e consultar o resultado.
- [ ] Verificar o isolamento do chatbot e habilitar a execução paralela com limite de sessões.

## Referências

- [Playwright para Python](https://playwright.dev/python/docs/library)
- [Instalação de navegadores no Playwright](https://playwright.dev/python/docs/browsers)
- [Gravador de interações do Playwright](https://playwright.dev/python/docs/codegen)
