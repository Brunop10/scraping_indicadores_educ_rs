import re
import time
from pathlib import Path

from selenium.webdriver.common.by import By

TEMPO_ESPERA_DOWNLOAD = 12
TENTATIVAS_DOWNLOAD = 2
INTERVALO_ENTRE_DOWNLOADS = 0.5
INTERVALO_DESCANSO = 2


def _nome_seguro(valor):
    nome = re.sub(r'[<>:"/\\|?*]+', "_", str(valor)).strip(" ._")
    return re.sub(r"\s+", "_", nome) or "sem_nome"


def _aguardar_download(
    diretorio,
    arquivos_anteriores,
    timeout=30,
    verificar_parada=None,
):
    limite = time.monotonic() + timeout

    while time.monotonic() < limite:
        downloads = [
            arquivo
            for arquivo in diretorio.glob("*.csv")
            if arquivo.name not in arquivos_anteriores
        ]
        pendentes = list(diretorio.glob("*.crdownload"))

        if downloads and not pendentes:
            return max(downloads, key=lambda arquivo: arquivo.stat().st_mtime)

        if verificar_parada:
            verificar_parada()
        time.sleep(0.2)

    raise TimeoutError(
        f"Nenhum CSV foi concluído em {timeout} segundos."
    )


def baixar_csvs_dos_graficos(
    driver,
    diretorio_temporario,
    diretorio_saida,
    codigo_secao,
    ano,
    rede,
    contexto=None,
    tentativas=TENTATIVAS_DOWNLOAD,
    timeout_download=TEMPO_ESPERA_DOWNLOAD,
    ignorar_canvas_sem_dados=False,
    ids_graficos=None,
    sobrescrever=False,
    verificar_parada=None,
):
    """Baixa CSVs dos gráficos e ignora a tabela de distribuição territorial."""
    diretorio_temporario = Path(diretorio_temporario)
    diretorio_saida = (
        Path(diretorio_saida)
        / _nome_seguro(codigo_secao)
        / _nome_seguro(f"ano_{ano or 'geral'}")
        / _nome_seguro(rede)
    )
    if contexto:
        diretorio_saida /= _nome_seguro(contexto)
    diretorio_saida.mkdir(parents=True, exist_ok=True)

    cards = driver.find_elements(By.CSS_SELECTOR, "#main-content .chart-card")
    total_baixados = 0

    for card in cards:
        if verificar_parada:
            verificar_parada()
        canvases = card.find_elements(By.CSS_SELECTOR, "canvas")
        if ids_graficos is not None and not any(
            canvas.get_attribute("id") in ids_graficos
            for canvas in canvases
        ):
            continue

        titulos = card.find_elements(By.CSS_SELECTOR, ".chart-title")
        botoes = card.find_elements(
            By.CSS_SELECTOR,
            ".chart-source button.export-btn--footer",
        )
        if not titulos or not botoes:
            continue

        titulo = titulos[0].text.strip() or "grafico"
        destino = diretorio_saida / f"{_nome_seguro(titulo)}.csv"
        if destino.exists() and not sobrescrever:
            print(f"  CSV já existe; ignorado: {destino.name}")
            continue

        if not canvases or not (canvases[0].get_attribute("style") or "").strip():
            print(f"  Canvas vazio; CSV ignorado: {titulo}")
            continue
        if ignorar_canvas_sem_dados:
            tem_dados = driver.execute_script(
                """
                const chart = Chart.getChart(arguments[0]);
                return Boolean(chart && chart.data.datasets.some(dataset =>
                    Array.isArray(dataset.data)
                    && dataset.data.some(valor => valor !== null && valor !== undefined)
                ));
                """,
                canvases[0],
            )
            if not tem_dados:
                print(f"  Gráfico sem dados; CSV ignorado: {titulo}")
                continue

        arquivo_baixado = None
        for tentativa in range(1, tentativas + 1):
            if verificar_parada:
                verificar_parada()
            arquivos_anteriores = {
                arquivo.name
                for arquivo in diretorio_temporario.glob("*.csv")
            }

            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});",
                botoes[0],
            )
            driver.execute_script("arguments[0].click();", botoes[0])

            try:
                arquivo_baixado = _aguardar_download(
                    diretorio_temporario,
                    arquivos_anteriores,
                    timeout=timeout_download,
                    verificar_parada=verificar_parada,
                )
                break
            except TimeoutError:
                if tentativa < tentativas:
                    print(f"  Sem arquivo; tentando novamente: {titulo}")
                    time.sleep(1)

        if arquivo_baixado is None:
            print(f"  CSV não baixado; seguindo: {titulo}")
            continue

        arquivo_baixado.replace(destino)
        total_baixados += 1
        print(f"  CSV salvo: {destino}")
        if total_baixados % 10 == 0:
            if verificar_parada:
                verificar_parada()
            time.sleep(INTERVALO_DESCANSO)
        else:
            if verificar_parada:
                verificar_parada()
            time.sleep(INTERVALO_ENTRE_DOWNLOADS)

    print(f"  CSVs baixados nesta combinação: {total_baixados}")
    return total_baixados