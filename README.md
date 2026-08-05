# Universal MUGEN Bot

Aplicativo portátil para executar lutas CPU vs CPU em sequência em diferentes compilações de **MUGEN**, **MugenHook**, jogos com executável renomeado e **IKEMEN GO**.

O objetivo é não depender de um arquivo chamado `mugen.exe` nem da estrutura rígida `chars/NOME/NOME.def`.

## O que esta primeira versão faz

- detecta automaticamente o executável principal, inclusive nomes como `MKDOTE.exe`;
- identifica MUGEN, MugenHook e IKEMEN GO;
- localiza `mugen.cfg`, `system.def`, `select.def` e `mugen.log` quando estão acessíveis;
- lê personagens e cenários do `select.def`;
- encontra `.def` em subpastas de `chars` e `stages`;
- aprende caminhos usados anteriormente pelo `mugen.log`;
- procura caminhos `chars/.../*.def` e `stages/.../*.def` dentro de executáveis empacotados;
- inicia CPU vs CPU por parâmetros de linha de comando;
- tenta três formatos de comando automaticamente;
- acompanha o ciclo da luta pelo `mugen.log`;
- fecha e abre a próxima luta automaticamente;
- continua depois de crashes e desativa temporariamente personagens que falharem repetidamente;
- não modifica os arquivos do jogo.

## Instalação rápida no Windows

### Opção recomendada: baixar o EXE pronto

1. Abra a guia **Actions** deste repositório.
2. Entre na execução mais recente chamada **Build Windows EXE**.
3. No final da página, em **Artifacts**, baixe `UniversalMugenBot-Windows`.
4. Extraia o ZIP baixado.
5. Execute `UniversalMugenBot.exe`.

O Windows SmartScreen pode mostrar um aviso porque o executável ainda não possui assinatura digital. Nesse caso, clique em **Mais informações** e depois em **Executar assim mesmo**, desde que o arquivo tenha sido baixado diretamente deste repositório.

O programa é portátil: não precisa ser instalado e pode ficar em qualquer pasta. Ele também não precisa ser copiado para dentro do jogo.

### Opção alternativa: executar pelo Python

1. Instale o Python 3.11 ou mais recente para Windows, marcando **Add Python to PATH**.
2. Baixe o código em **Code → Download ZIP** e extraia.
3. Execute `run.bat`.

## Uso no Mortal Kombat Defenders of the Earth

1. Abra `UniversalMugenBot.exe`.
2. Clique em **Escolher pasta**.
3. Escolha a pasta que contém:

   ```text
   MKDOTE.exe
   MugenhookSettings.ini
   mugen.log
   data\
   plugins\
   ```

4. Deixe marcada a opção **Procurar personagens dentro do EXE**. O executável desse jogo tem aproximadamente 1,76 GB, então a primeira análise pode levar alguns minutos.
5. Aguarde aparecer a quantidade de personagens detectados.
6. Primeiro clique em **Testar uma luta**.
7. Quando o teste iniciar corretamente, clique em **Iniciar lutas automáticas**.
8. Use **Parar** para encerrar o ciclo.

Configuração inicial indicada para esse jogo:

```text
Rounds: 1
IA: 8
Intervalo: 2
Método: auto
Procurar personagens dentro do EXE: marcado
```

O perfil fica salvo em `%APPDATA%\UniversalMugenBot\profiles.json`.

## Executar pelo código-fonte

Requer Python 3.11 ou mais recente no Windows.

```bat
run.bat
```

Ou diretamente:

```bat
python universal_mugen_bot.py
```

A interface usa apenas a biblioteca padrão do Python. Nenhum pacote externo é necessário para executar pelo código-fonte.

## Gerar o EXE

```bat
build_windows.bat
```

O arquivo será criado em:

```text
dist\UniversalMugenBot.exe
```

## Como a detecção funciona

A ordem de preferência é:

1. `select.def` indicado pelo `system.def`;
2. pastas `chars` e `stages`;
3. caminhos registrados no `mugen.log`;
4. busca de strings dentro do executável empacotado.

Para iniciar uma luta, o modo **auto** tenta formatos diferentes de parâmetros e confirma pelo log se a luta realmente começou. Ao encontrar `End of match loop` ou a tela de vitória, encerra o processo e inicia outra combinação.

## Limitações atuais

- compilações que bloqueiam completamente parâmetros de linha de comando podem exigir um perfil de automação por teclado;
- arquivos internos comprimidos ou criptografados podem não aparecer na busca de strings do executável;
- a primeira versão acompanha começo e fim da luta, mas ainda não registra o vencedor;
- o teste real precisa ser feito no Windows, junto ao executável do jogo.

## Segurança

O aplicativo não contém jogos, personagens, cenários ou arquivos protegidos. Ele trabalha somente com a instalação que já existe no computador do usuário.

## Créditos

Projeto original inspirado na ideia de gerenciamento automático do projeto [MugenBot](https://github.com/zeak6464/MugenBot), sem incorporar os arquivos do jogo.

## Licença

MIT.
