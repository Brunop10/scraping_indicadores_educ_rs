# Scraper de Indicadores Educacionais do RS

Aplicação em Python para coletar dados do [Painel de Indicadores da Educação Gaúcha](https://indicadores.educacao.rs.gov.br/). A interface permite escolher a CRE, o município, as seções e as redes que serão coletadas.

## Requisitos

- Python 3 instalado
- Google Chrome instalado

## Instalação

No terminal, dentro da pasta do projeto:

```powershell
py -m pip install selenium pandas openpyxl
```

O Tkinter, usado na interface, normalmente já acompanha a instalação do Python para Windows.

## Como executar

```powershell
py interface_scraping.py
```

Selecione a CRE e o município. Quando as opções forem carregadas, marque as seções e redes desejadas e clique em **Scraping**. Os botões **Todas** e **Nenhuma** facilitam a seleção.

Durante a coleta, **Parar e salvar parcial** solicita a interrupção e mantém os dados coletados até aquele momento. Se a coleta falhar, o scraper pode tentar retomá-la usando o checkpoint.

## Arquivos gerados

Os resultados são salvos na pasta `output`, criada automaticamente:

- Planilhas Excel (`.xlsx`) e checkpoints: `output\`
- CSVs: `output\dados_csv\`
- Downloads temporários: `output\.temporarios\`

Os arquivos em `output` são resultados locais da execução e não devem ser enviados ao GitHub.

## Estrutura do projeto

- `interface_scraping.py`: interface gráfica
- `scraping_selenium.py`: fluxo principal de coleta
- `scraping_csv.py`: exportação dos gráficos em CSV
- `scraping_excel.py`: gravação da planilha Excel
- `scraping_fluxo.py`: coleta dos gráficos de fluxo
- `scraping_indicadores.py`: leitura e filtragem dos indicadores
- `scraping_checkpoint.py`: retomada da coleta

## Preparar para publicar no GitHub

O arquivo `.gitignore` do projeto exclui resultados gerados e arquivos temporários. Antes de criar o commit, confira o que será enviado:

```powershell
git init
git status
git add .
git status
git commit -m "Initial project setup"
```

Depois, crie um repositório vazio no GitHub e configure-o como remoto:

```powershell
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/NOME_DO_REPOSITORIO.git
git push -u origin main
```

Substitua a URL pelo endereço do seu repositório. Revise `git status` antes do commit e não publique credenciais, dados coletados ou arquivos privados.

## Observação

O scraper depende da estrutura e disponibilidade do painel. Use-o de forma responsável e respeite as condições de uso e as orientações do site de origem.
