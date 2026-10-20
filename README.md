# Nature Conversor

Converte partituras em PDF (ou imagem) e arquivos **MIDI** para **MuseScore (.mscz)** e **MusicXML**, com uma janela simples para Windows.
Usa o [Audiveris](https://github.com/Audiveris/audiveris) para o reconhecimento e o MuseScore instalado no computador para gerar o `.mscz` e converter os MIDI.

## Baixar

Vá em **[Releases](../../releases/latest)**, baixe `NatureConversor-Windows.zip`, descompacte e abra `Nature Conversor.exe`.
Não precisa instalar Python nem Audiveris.

## Como o executável é gerado

O GitHub Actions (`.github/workflows/gerar-executavel.yml`) roda num Windows virtual a cada alteração em `main`:

1. baixa a versão Console mais recente do Audiveris e extrai sem instalar;
2. baixa os dicionários de texto (português, inglês, latim, italiano);
3. empacota o app com PyInstaller;
4. publica o ZIP portátil em *Releases*.

## Licença

AGPL-3.0, a mesma do Audiveris.
