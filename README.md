# Universal MUGEN Bot

Aplicativo portátil para executar lutas CPU vs CPU em sequência em diferentes compilações de **MUGEN**, **MugenHook**, executáveis renomeados e **IKEMEN GO**.

## Download

Baixe a versão mais recente em **Releases**:

- `UniversalMugenBot-Windows.zip` — recomendado;
- `UniversalMugenBot.exe` — executável direto;
- `UniversalMugenBot.exe.sha256` — verificação de integridade.

Versão atual: **v0.2.0**.

## Novidades da v0.2.0

- encontra automaticamente a raiz real do jogo, mesmo quando o usuário seleciona uma pasta-pai;
- procura executáveis em subpastas como `Game`, `bin`, `Mugen` e `Ikemen`;
- localiza o `mugen.cfg` ativo;
- segue a configuração `motif` até o `system.def` correto;
- segue o `system.def` até o `select.def` realmente usado pelo jogo;
- procura pastas `chars` e `stages` em layouts diferentes;
- valida o conteúdo dos arquivos `.def` antes de classificá-los;
- ignora storyboards como `intro.def`, `ending.def`, créditos e telas de encerramento;
- aceita do log somente personagens que terminaram com `Character ... loaded OK`;
- corrige o parâmetro `-s` para evitar caminhos como `stages/stages/arena.def`;
- identifica imediatamente mensagens como `Can't open stage` e não trata erros como lutas iniciadas;
- remove cenários quebrados da sessão e continua com os cenários válidos;
- procura referências em arquivos de configuração, executáveis e pacotes como `Elecbyte.MUGEN.libs`;
- lê caminhos empacotados tanto em ASCII quanto em UTF-16;
- não modifica os arquivos originais do jogo.

## Instalação no Windows

1. Abra a seção **Releases**.
2. Entre na versão mais recente.
3. Baixe `UniversalMugenBot-Windows.zip`.
4. Extraia o arquivo.
5. Execute `UniversalMugenBot.exe`.

O programa é portátil e pode ficar em qualquer pasta. Não precisa ser copiado para dentro do MUGEN.

O Windows SmartScreen pode mostrar um aviso porque o executável não possui assinatura digital. Quando o arquivo vier diretamente deste repositório, use **Mais informações → Executar assim mesmo**.

## Como usar

1. Abra `UniversalMugenBot.exe`.
2. Clique em **Escolher pasta**.
3. Selecione a pasta do jogo ou uma pasta-pai que contenha a instalação.
4. Mantenha **Procurar personagens dentro do EXE** marcado para jogos empacotados.
5. Clique em **Analisar**.
6. Confira no log quais arquivos foram reconhecidos como configuração, motif e roster ativos.
7. Clique em **Testar uma luta**.
8. Funcionando, use **Iniciar lutas automáticas**.

Configuração inicial indicada:

```text
Rounds: 1
IA: 8
Intervalo: 2
Método: auto
```

O perfil fica salvo em:

```text
%APPDATA%\UniversalMugenBot\profiles.json
```

## Como a detecção funciona

A análise segue esta ordem:

1. executável real e pasta de execução;
2. `mugen.cfg` ou configuração do IKEMEN;
3. `motif` configurado no `mugen.cfg`;
4. `system.def` e seu `select.def`;
5. diretórios reais `chars` e `stages`;
6. personagens carregados com sucesso no log;
7. arquivos de configuração e pacotes internos;
8. executável empacotado, quando necessário.

Personagens e cenários só entram no sorteio depois de passarem por validações estruturais. Isso evita usar storyboards, arquivos auxiliares e caminhos inválidos.

## Executar pelo código-fonte

Requer Python 3.11 ou mais recente:

```bat
run.bat
```

Ou:

```bat
python universal_mugen_bot.py
```

## Gerar o EXE

```bat
build_windows.bat
```

O arquivo será criado em:

```text
dist\UniversalMugenBot.exe
```

## Limitações

Não existe uma garantia literal de compatibilidade com 100% das compilações modificadas. Jogos que criptografam os assets, bloqueiam parâmetros de linha de comando ou usam menus totalmente personalizados podem precisar de um adaptador específico. Nesses casos, o programa agora informa a limitação sem iniciar uma luta com dados inválidos.

## Segurança

O aplicativo não contém jogos, personagens ou cenários. Ele trabalha somente com a instalação existente no computador e não altera `select.def`, `system.def`, personagens ou stages.

## Créditos

Projeto inspirado na ideia de gerenciamento automático do projeto [MugenBot](https://github.com/zeak6464/MugenBot), com implementação própria para detecção universal.

## Licença

MIT.
