import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import traceback
import unicodedata

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    StaleElementReferenceException,
    WebDriverException,
)
import pandas as pd
from scraping_csv import baixar_csvs_dos_graficos
from scraping_checkpoint import GerenciadorCheckpoint
from scraping_excel import salvar_dados_excel
from scraping_indicadores import (
    filtrar_indicadores_municipais,
    filtrar_kpis_por_mudanca,
    ler_cartoes_kpi,
)
from scraping_fluxo import (
    baixar_csvs_fluxo_rendimento,
    selecionar_valor_filtro,
)

for fluxo in (sys.stdout, sys.stderr):
    reconfigurar = getattr(fluxo, "reconfigure", None)
    if callable(reconfigurar):
        reconfigurar(encoding="utf-8", errors="replace")

URL = "https://indicadores.educacao.rs.gov.br/"
MAX_TENTATIVAS_RETOMADA = 3

DIRETORIO_PROJETO = Path(__file__).resolve().parent
DIRETORIO_OUTPUT = DIRETORIO_PROJETO / "output"
DIRETORIO_CSV = DIRETORIO_OUTPUT / "dados_csv"
DIRETORIO_DOWNLOAD_TEMP = DIRETORIO_OUTPUT / ".temporarios"
DIRETORIO_OUTPUT.mkdir(parents=True, exist_ok=True)
DIRETORIO_DOWNLOAD_TEMP.mkdir(parents=True, exist_ok=True)

parser = argparse.ArgumentParser()
parser.add_argument(
    "--iniciar-em",
    metavar="SECAO",
    help="Código da seção inicial, por exemplo: infra.",
)
parser.add_argument(
    "--descartar-checkpoint",
    action="store_true",
    help="Descarta o checkpoint atual e inicia uma nova coleta.",
)
parser.add_argument(
    "--cre-id",
    default="21",
    help="Código da CRE no site (padrão: 21).",
)
parser.add_argument(
    "--cre-nome",
    default="21 CRE - Três Passos",
    help="Nome da CRE selecionada.",
)
parser.add_argument(
    "--municipio",
    default="Santo Augusto",
    help="Município a ser coletado (padrão: Santo Augusto).",
)
parser.add_argument(
    "--sem-pausa",
    action="store_true",
    help="Não aguarda Enter ao terminar (útil ao iniciar pela interface).",
)
parser.add_argument(
    "--config-json",
    help="Arquivo JSON com filtros de seção e rede.",
)
parser.add_argument(
    "--arquivo-parada",
    help="Arquivo-sinal criado pela interface para interromper a coleta.",
)
argumentos_cli = parser.parse_args()

configuracao_scraping = {}
if argumentos_cli.config_json:
    configuracao_scraping = json.loads(
        Path(argumentos_cli.config_json).read_text(encoding="utf-8")
    )


def nome_arquivo_seguro(texto):
    texto_ascii = unicodedata.normalize("NFKD", texto)
    texto_ascii = texto_ascii.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", texto_ascii).strip("_").lower()


slug_municipio = nome_arquivo_seguro(argumentos_cli.municipio)
configuracao_completa = json.dumps(
    configuracao_scraping,
    ensure_ascii=False,
    sort_keys=True,
)
hash_configuracao = hashlib.sha256(
    configuracao_completa.encode("utf-8")
).hexdigest()[:8]
configuracao_padrao = not configuracao_scraping
if (
    configuracao_padrao
    and argumentos_cli.cre_id == "21"
    and slug_municipio == "santo_augusto"
):
    nome_checkpoint = "checkpoint_scraping.json"
else:
    nome_checkpoint = (
        f"checkpoint_{argumentos_cli.cre_id}_{slug_municipio}_"
        f"{hash_configuracao}.json"
    )
ARQUIVO_CHECKPOINT = DIRETORIO_OUTPUT / nome_checkpoint
checkpoint_legado = DIRETORIO_PROJETO / nome_checkpoint
if not ARQUIVO_CHECKPOINT.exists() and checkpoint_legado.exists():
    checkpoint_legado.replace(ARQUIVO_CHECKPOINT)

NOME_ARQUIVO_EXCEL = DIRETORIO_OUTPUT / (
    "dados_santo_augusto.xlsx"
    if configuracao_padrao
    and argumentos_cli.cre_id == "21"
    and slug_municipio == "santo_augusto"
    else (
        f"dados_{slug_municipio}_cre_{argumentos_cli.cre_id}_"
        f"{hash_configuracao}.xlsx"
        if configuracao_scraping
        else (
            f"dados_{slug_municipio}.xlsx"
            if argumentos_cli.cre_id == "21"
            and slug_municipio == "santo_augusto"
            else f"dados_{slug_municipio}_cre_{argumentos_cli.cre_id}.xlsx"
        )
    )
)


class ScrapingInterrompido(Exception):
    """Interrupção solicitada pela interface após salvar o progresso."""


def verificar_parada():
    if argumentos_cli.arquivo_parada and Path(
        argumentos_cli.arquivo_parada
    ).exists():
        raise ScrapingInterrompido("Parada solicitada pelo usuário.")


codigos_secao_selecionados = set(
    configuracao_scraping.get("secoes", [])
)
indicadores_por_secao = configuracao_scraping.get("indicadores")
codigos_rede_selecionados = set(configuracao_scraping.get("redes", []))
ano_inicial = configuracao_scraping.get("ano_inicio")
ano_final = configuracao_scraping.get("ano_fim")
if (ano_inicial is None) != (ano_final is None):
    raise ValueError("O intervalo precisa informar ano inicial e final.")
if ano_inicial is not None and int(ano_inicial) > int(ano_final):
    raise ValueError("O ano inicial não pode ser maior que o ano final.")

gerenciador_checkpoint = GerenciadorCheckpoint(
    ARQUIVO_CHECKPOINT,
    secao_inicial=argumentos_cli.iniciar_em,
    descartar=argumentos_cli.descartar_checkpoint,
)
estado_checkpoint = gerenciador_checkpoint.estado

opcoes_chrome = webdriver.ChromeOptions()
opcoes_chrome.add_experimental_option(
    "prefs",
    {
        "download.default_directory": str(DIRETORIO_DOWNLOAD_TEMP.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "profile.default_content_setting_values.automatic_downloads": 1,
        "safebrowsing.enabled": True,
    },
)

driver = webdriver.Chrome(options=opcoes_chrome)
wait = WebDriverWait(driver, 20)
wait_rede = WebDriverWait(
    driver,
    60,
    ignored_exceptions=(StaleElementReferenceException,)
)

dados_por_secao = {}
for titulo, registros in estado_checkpoint["dados_por_secao"].items():
    if registros:
        dados_por_secao[titulo] = pd.DataFrame(registros)

unidades_concluidas = set(estado_checkpoint["unidades_concluidas"])


def salvar_checkpoint():
    gerenciador_checkpoint.salvar(unidades_concluidas, dados_por_secao)


def salvar_saida_parcial():
    if dados_por_secao:
        salvar_dados_excel(dados_por_secao, NOME_ARQUIVO_EXCEL)
        print(f"Saída parcial salva: {NOME_ARQUIVO_EXCEL}")
    else:
        print("Nenhum KPI foi coletado; não há planilha parcial para gerar.")
    salvar_checkpoint()


def filtrar_indicadores_selecionados(codigo_secao, registros):
    if indicadores_por_secao is None:
        return registros
    selecionados = indicadores_por_secao.get(codigo_secao)
    if selecionados is None:
        return registros
    return [
        registro
        for registro in registros
        if registro.get("Indicador") in selecionados
    ]


def chave_unidade(*partes):
    return json.dumps(partes, ensure_ascii=False)


def iniciar_unidade(chave, **contexto):
    gerenciador_checkpoint.iniciar_unidade(
        chave,
        unidades_concluidas,
        dados_por_secao,
        **contexto,
    )


def concluir_unidade(chave):
    gerenciador_checkpoint.concluir_unidade(
        chave,
        unidades_concluidas,
        dados_por_secao,
    )


def selecionar_cre():
    """Seleciona a CRE configurada sem aplicar ainda o filtro municipal."""
    campo_cre = wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "#main-content #sel-cre")
        )
    )

    Select(campo_cre).select_by_value(argumentos_cli.cre_id)
    campo_municipio = wait.until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "#main-content #mun-search-input")
        )
    )
    campo_municipio.clear()
    campo_municipio.send_keys(argumentos_cli.municipio)

    wait.until(
        lambda d: d.execute_script(
            """
            return Array.from(document.querySelectorAll(
                "#main-content #mun-dropdown-list .mun-dd-item"
            )).some(opcao =>
                opcao.textContent.trim().toLocaleLowerCase() ===
                arguments[0].toLocaleLowerCase()
            );
            """,
            argumentos_cli.municipio,
        )
    )
    driver.execute_async_script(
        """
        const concluir = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(concluir));
        """
    )


def selecionar_municipio():
    """Seleciona o município configurado dentro da CRE selecionada."""

    # Aguarda a atualização das opções de municípios
    campo_municipio = wait.until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "#main-content #mun-search-input")
        )
    )

    campo_municipio.clear()
    campo_municipio.send_keys(argumentos_cli.municipio)

    municipio_selecionado = driver.execute_script(
        """
        const nomeMunicipio = arguments[0].toLocaleLowerCase();
        const opcao = Array.from(document.querySelectorAll(
            "#main-content #mun-dropdown-list .mun-dd-item"
        )).find(item =>
            item.textContent.trim().toLocaleLowerCase() === nomeMunicipio
        );
        if (!opcao) return false;
        opcao.click();
        return true;
        """,
        argumentos_cli.municipio,
    )
    if not municipio_selecionado:
        raise RuntimeError(
            f"{argumentos_cli.municipio} apareceu no dropdown, mas "
            "não foi possível selecioná-lo."
        )

    # Confirma o filtro aplicado sem depender do contêiner de cartões.
    wait.until(
        lambda d: d.execute_script(
            """
            const filtros = document.querySelector("#mun-filter-slot");
            return filtros?.innerText.includes(arguments[0]) || false;
            """,
            argumentos_cli.municipio,
        )
    )
    driver.execute_async_script(
        """
        const concluir = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(concluir));
        """
    )

    print(
        f"{argumentos_cli.municipio} selecionado na "
        f"{argumentos_cli.cre_nome}."
    )


def limpar_filtros_geograficos():
    """Remove os filtros de CRE e município para ler os valores gerais."""
    for tipo_filtro in ("mun", "cre"):
        seletor_chip = (
            f'#mun-filter-slot .filter-chip[data-clear="{tipo_filtro}"]'
        )
        chips = driver.find_elements(By.CSS_SELECTOR, seletor_chip)
        if chips:
            driver.execute_script("arguments[0].click();", chips[0])
            wait.until(
                EC.invisibility_of_element_located(
                    (By.CSS_SELECTOR, seletor_chip)
                )
            )

    driver.execute_async_script(
        """
        const concluir = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(concluir));
        """
    )


def detectar_indicadores_municipais(
    titulo_secao,
    ano,
    nome_rede,
    seletor_cartoes,
    etapa=None,
    componente=None,
):
    """Compara valores gerais, da CRE e do município selecionados."""
    dados_gerais = ler_cartoes_kpi(
        driver,
        titulo_secao,
        ano,
        nome_rede=nome_rede,
        etapa=etapa,
        componente=componente,
        seletor_cartoes=seletor_cartoes,
    )
    selecionar_cre()
    dados_cre = ler_cartoes_kpi(
        driver,
        titulo_secao,
        ano,
        nome_rede=nome_rede,
        etapa=etapa,
        componente=componente,
        seletor_cartoes=seletor_cartoes,
    )
    filtrar_kpis_por_mudanca(
        dados_gerais,
        dados_cre,
        f"CRE {argumentos_cli.cre_id}",
    )

    selecionar_municipio()
    dados_municipio = ler_cartoes_kpi(
        driver,
        titulo_secao,
        ano,
        nome_rede=nome_rede,
        etapa=etapa,
        componente=componente,
        seletor_cartoes=seletor_cartoes,
    )
    dados_municipais = filtrar_kpis_por_mudanca(
        dados_cre,
        dados_municipio,
        argumentos_cli.municipio,
    )
    return {
        registro["Indicador"]
        for registro in dados_municipais
        if registro.get("Indicador")
    }


def selecionar_rede(codigo_rede):
    """Clica no botão da rede e espera o painel confirmar a troca."""

    wait_rede.until(
        EC.invisibility_of_element_located(
            (By.ID, "rede-loading")
        )
    )

    seletor_botao = (
        "#main-content #rede-toggle "
        f'.rede-toggle-btn[data-rede="{codigo_rede}"]'
    )
    botao_rede = wait_rede.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, seletor_botao))
    )
    classe_botao = botao_rede.get_attribute("class") or ""
    if "active" not in classe_botao:
        driver.execute_script("arguments[0].click();", botao_rede)

    wait_rede.until(
        lambda d: d.execute_script(
            """
            const ativo = document.querySelector(
                "#main-content #rede-toggle .rede-toggle-btn.active"
            );
            return ativo?.getAttribute("data-rede") === arguments[0];
            """,
            codigo_rede,
        )
    )
    wait_rede.until(
        EC.invisibility_of_element_located(
            (By.ID, "rede-loading")
        )
    )
    print(f"  Rede confirmada no painel: {codigo_rede}")


def rede_sem_dados_na_secao():
    """Detecta a mensagem do painel para redes sem dados na seção atual."""
    return driver.execute_script(
        """
        const principal = document.querySelector("#main-content");
        return Array.from(principal?.querySelectorAll("p") || []).some(
            paragrafo =>
                /dados(?: de)? .+ não disponíveis para a rede/i.test(
                    paragrafo.textContent.trim()
                )
        );
        """
    )


def selecionar_ano(ano):
    """Seleciona o ano e aguarda a atualização síncrona da página."""

    seletor_ano = wait.until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "#main-content #sel-ano")
        )
    )
    Select(seletor_ano).select_by_value(ano)
    driver.execute_async_script(
        """
        const concluir = arguments[arguments.length - 1];
        requestAnimationFrame(() => requestAnimationFrame(concluir));
        """
    )


def obter_titulo_secao(nome_sidebar):
    """Obtém o título exibido no cabeçalho da seção."""

    try:
        titulo_elemento = wait.until(
            EC.presence_of_element_located((
                By.CSS_SELECTOR,
                ".section-banner-left h2"
            ))
        )

        titulo = driver.execute_script(
            """
            const elemento = arguments[0];
            const texto = Array.from(elemento.childNodes)
                .find(no => no.nodeType === Node.TEXT_NODE);

            return texto
                ? texto.textContent.trim()
                : elemento.textContent.trim();
            """,
            titulo_elemento
        )

        return titulo if titulo else nome_sidebar

    except Exception:
        return nome_sidebar


def extrair_secao(
    nome_sidebar,
    codigo_secao,
    seletor_cartoes="#main-content .kpi-strip .kpi-card"
):
    """Extrai os cartões da seção atual para cada rede."""

    seletor_saers = "#main-content #saers-kpi-cards .kpi-card"
    redes_saers_disponiveis = {"estadual", "municipal", "todas"}
    titulo_secao = obter_titulo_secao(nome_sidebar)
    dados_secao = (
        dados_por_secao[titulo_secao].to_dict(orient="records")
        if titulo_secao in dados_por_secao
        else []
    )

    print(f"Título da seção: {titulo_secao}")

    botoes_rede = driver.find_elements(
        By.CSS_SELECTOR,
        "#main-content #rede-toggle .rede-toggle-btn"
    )

    redes = []
    

    for botao in botoes_rede:
        codigo = botao.get_attribute("data-rede")
        nome = botao.text.strip()

        if codigo and (
            seletor_cartoes != seletor_saers
            or codigo in redes_saers_disponiveis
        ):
            redes.append((codigo, nome))

    # Caso a seção não possua seletor de redes
    if not redes:
        redes = [(None, "Não informada")]

    botoes_rede_ativos = [
        botao for botao in botoes_rede
        if "active" in (botao.get_attribute("class") or "")
    ]
    nome_rede_referencia = (
        botoes_rede_ativos[0].text.strip()
        if botoes_rede_ativos
        else redes[0][1]
    )
    seletores_ano_referencia = driver.find_elements(
        By.CSS_SELECTOR,
        "#main-content #sel-ano",
    )
    ano_referencia = (
        seletores_ano_referencia[0].get_attribute("value")
        if seletores_ano_referencia
        else ""
    )
    if seletor_cartoes == seletor_saers:
        wait.until(
            lambda d: d.execute_script(
                "return document.querySelectorAll(arguments[0]).length;",
                seletor_cartoes,
            ) > 0
        )
    indicadores_municipais = detectar_indicadores_municipais(
        titulo_secao,
        ano_referencia,
        nome_rede_referencia,
        seletor_cartoes,
    )
    print(
        f"Indicadores municipais identificados em {nome_rede_referencia}: "
        f"{len(indicadores_municipais)}"
    )

    botoes_rede = driver.find_elements(
        By.CSS_SELECTOR,
        "#main-content #rede-toggle .rede-toggle-btn",
    )
    redes = []
    for botao in botoes_rede:
        codigo = botao.get_attribute("data-rede")
        nome = botao.text.strip()
        if codigo and botao.is_enabled() and (
            seletor_cartoes != seletor_saers
            or codigo in redes_saers_disponiveis
        ) and (
            not codigos_rede_selecionados
            or codigo in codigos_rede_selecionados
        ):
            redes.append((codigo, nome))

    if not redes:
        if codigos_rede_selecionados:
            print(
                f"  Nenhuma das redes selecionadas está disponível em "
                f"{titulo_secao}; pulando seção."
            )
            return
        redes = [(None, "Não informada")]

    for codigo_rede, nome_rede in redes:

        print(f"Extraindo rede: {nome_rede}")

        if codigo_rede:
            selecionar_rede(codigo_rede)

        if rede_sem_dados_na_secao():
            print(
                f"  Dados não disponíveis para {nome_rede} em "
                f"{titulo_secao}; pulando rede."
            )
            continue

        anos = []

        if codigo_secao != "docencia":
            seletores_ano = driver.find_elements(
                By.CSS_SELECTOR,
                "#main-content #sel-ano"
            )

            if seletores_ano:
                anos = [
                    opcao.get_attribute("value")
                    for opcao in Select(seletores_ano[0]).options
                    if opcao.get_attribute("value")
                ]

        if not anos:
            anos = [""]
            print(
                "  "
                + (
                    "Docência não usa seletor de ano; "
                    if codigo_secao == "docencia"
                    else "Seletor de ano ausente ou sem opções; "
                )
                + "extraindo a seção sem filtro de ano."
            )

        for ano in anos:
            verificar_parada()
            if codigo_secao == "inse" and ano == "2021":
                print("  Pulando 2021 em Contexto Socioeconômico (INSE).")
                continue
            if ano_inicial is not None and ano:
                try:
                    ano_numero = int(ano)
                except ValueError:
                    continue
                if not int(ano_inicial) <= ano_numero <= int(ano_final):
                    continue

            chave = chave_unidade(
                "secao",
                codigo_secao,
                codigo_rede,
                ano,
            )
            if chave in unidades_concluidas:
                print(f"  Unidade já concluída; pulando ano {ano or 'geral'}.")
                continue
            iniciar_unidade(
                chave,
                secao=codigo_secao,
                rede=nome_rede,
                ano=ano,
            )

            if ano:
                selecionar_ano(ano)

            if seletor_cartoes == seletor_saers:
                wait.until(
                    lambda d: d.execute_script(
                        "return document.querySelectorAll(arguments[0]).length;",
                        seletor_cartoes
                    ) > 0
                )

            dados_rede = ler_cartoes_kpi(
                driver,
                titulo_secao,
                ano,
                nome_rede=nome_rede,
                seletor_cartoes=seletor_cartoes
            )
            dados_rede = filtrar_indicadores_municipais(
                dados_rede,
                indicadores_municipais,
            )
            dados_rede = filtrar_indicadores_selecionados(
                codigo_secao,
                dados_rede,
            )

            print(
                f"  {f'Ano {ano}: ' if ano else ''}"
                f"{len(dados_rede)} cartões encontrados"
            )
            dados_secao.extend(dados_rede)
            if dados_secao:
                dados_por_secao[titulo_secao] = (
                    pd.DataFrame(dados_secao).drop_duplicates()
                )
            verificar_parada()
            salvar_checkpoint()

            if codigo_secao == "infra":
                categorias_infra = [
                    (botao.get_attribute("data-cat"), botao.text.strip())
                    for botao in driver.find_elements(
                        By.CSS_SELECTOR,
                        "#infra-cat-tabs .infra-cat-tab",
                    )
                    if botao.get_attribute("data-cat")
                ]

                for codigo_categoria, nome_categoria in categorias_infra:
                    verificar_parada()
                    selecionada = driver.execute_script(
                        """
                        const categoria = arguments[0];
                        const botao = Array.from(document.querySelectorAll(
                            "#infra-cat-tabs .infra-cat-tab"
                        )).find(item => item.dataset.cat === categoria);
                        if (!botao) return false;
                        if (!botao.classList.contains("active")) {
                            botao.click();
                        }
                        return true;
                        """,
                        codigo_categoria,
                    )
                    if not selecionada:
                        print(
                            "  Categoria de infraestrutura não encontrada: "
                            f"{nome_categoria}"
                        )
                        continue

                    wait.until(
                        lambda d: d.execute_script(
                            """
                            const titulo = document.querySelector(
                                "#infra-chart-title"
                            )?.textContent.trim() || "";
                            return titulo.startsWith(arguments[0])
                                && (!arguments[1]
                                    || titulo.includes(`vs ${arguments[1]}`));
                            """,
                            nome_categoria,
                            ano,
                        )
                    )
                    baixar_csvs_dos_graficos(
                        driver=driver,
                        diretorio_temporario=DIRETORIO_DOWNLOAD_TEMP,
                        diretorio_saida=DIRETORIO_CSV,
                        codigo_secao=codigo_secao,
                        ano=ano,
                        rede=nome_rede,
                        contexto=nome_categoria,
                        verificar_parada=verificar_parada,
                    )
            elif codigo_secao == "fluxo":
                verificar_parada()
                baixar_csvs_fluxo_rendimento(
                    driver,
                    wait,
                    DIRETORIO_DOWNLOAD_TEMP,
                    DIRETORIO_CSV,
                    baixar_csvs_dos_graficos,
                    ano,
                    nome_rede,
                    verificar_parada=verificar_parada,
                )
            else:
                verificar_parada()
                baixar_csvs_dos_graficos(
                    driver=driver,
                    diretorio_temporario=DIRETORIO_DOWNLOAD_TEMP,
                    diretorio_saida=DIRETORIO_CSV,
                    codigo_secao=codigo_secao,
                    ano=ano,
                    rede=nome_rede,
                    verificar_parada=verificar_parada,
                )

            concluir_unidade(chave)

    if dados_secao:
        dados_por_secao[titulo_secao] = (
            pd.DataFrame(dados_secao).drop_duplicates()
        )

        print(
            f"Seção concluída: {titulo_secao} | "
            f"{len(dados_por_secao[titulo_secao])} registros"
        )
    else:
        print(f"Nenhum indicador extraído de {titulo_secao}.")


def aguardar_kpis_desigualdades(nome_etapa, codigo_componente, nome_componente):
    return wait_rede.until(
        lambda d: d.execute_script(
            """
            const cards = document.querySelectorAll(".kpi-strip .kpi-card");
            const legenda = cards.length
                ? cards[cards.length - 1]
                    .querySelector(".kpi-abs")?.textContent || ""
                : "";
            return legenda.includes(arguments[0])
                && (
                    legenda.includes(arguments[1])
                    || legenda.includes(arguments[2])
                );
            """,
            nome_etapa,
            codigo_componente,
            nome_componente,
        )
    )


def extrair_desigualdades(nome_sidebar):
    """Extrai os indicadores para cada etapa e componente curricular."""

    titulo_secao = obter_titulo_secao(nome_sidebar)
    seletor_etapa = wait.until(
        EC.presence_of_element_located(
            (By.ID, "desig-sel-etapa")
        )
    )
    seletor_componente = wait.until(
        EC.presence_of_element_located(
            (By.ID, "desig-sel-disc")
        )
    )

    etapas = [
        (opcao.get_attribute("value"), opcao.text.strip())
        for opcao in Select(seletor_etapa).options
    ]
    codigos_componentes = [
        opcao.get_attribute("value")
        for opcao in Select(seletor_componente).options
        if opcao.get_attribute("value")
    ]
    anos_disponiveis = [
        opcao.get_attribute("value")
        for opcao in Select(
            wait.until(
                EC.presence_of_element_located((By.ID, "sel-ano"))
            )
        ).options
        if opcao.get_attribute("value")
    ]
    if not anos_disponiveis:
        anos = [""]
    elif ano_inicial is not None:
        anos = [
            ano for ano in anos_disponiveis
            if ano.isdigit()
            and int(ano_inicial) <= int(ano) <= int(ano_final)
        ]
    else:
        anos = anos_disponiveis
    redes_ativas = driver.find_elements(
        By.CSS_SELECTOR,
        "#main-content #rede-toggle .rede-toggle-btn.active"
    )
    nome_rede = redes_ativas[0].text.strip() if redes_ativas else "Não informada"
    dados_secao = (
        dados_por_secao[titulo_secao].to_dict(orient="records")
        if titulo_secao in dados_por_secao
        else []
    )

    if etapas and codigos_componentes:
        if anos[0]:
            selecionar_ano(anos[0])
        selecionar_valor_filtro(driver, "desig-sel-etapa", etapas[0][0])
        selecionar_valor_filtro(
            driver,
            "desig-sel-disc",
            codigos_componentes[0],
        )
        nome_componente_inicial = driver.execute_script(
            """
            return document.querySelector(
                `#desig-sel-disc option[value="${arguments[0]}"]`
            )?.textContent.trim() || "";
            """,
            codigos_componentes[0],
        )
        aguardar_kpis_desigualdades(
            etapas[0][1],
            codigos_componentes[0],
            nome_componente_inicial,
        )
        ano_referencia = driver.find_element(By.ID, "sel-ano").get_attribute(
            "value"
        )
        indicadores_municipais = detectar_indicadores_municipais(
            titulo_secao,
            ano_referencia,
            nome_rede,
            "#main-content .kpi-strip .kpi-card",
            etapa=etapas[0][1],
            componente=nome_componente_inicial,
        )
    else:
        indicadores_municipais = set()

    print(
        "Indicadores municipais identificados em "
        f"{nome_rede}: {len(indicadores_municipais)}"
    )

    for ano in anos:
        verificar_parada()
        if ano:
            selecionar_ano(ano)
        for codigo_etapa, nome_etapa in etapas:
            verificar_parada()
            if ano:
                selecionar_ano(ano)
            selecionar_valor_filtro(driver, "desig-sel-etapa", codigo_etapa)

            for codigo_componente in codigos_componentes:
                verificar_parada()
                selecionar_valor_filtro(
                    driver,
                    "desig-sel-disc",
                    codigo_componente,
                )
                nome_componente = driver.execute_script(
                    """
                    return document.querySelector(
                        `#desig-sel-disc option[value="${arguments[0]}"]`
                    )?.textContent.trim() || "";
                    """,
                    codigo_componente,
                )

                aguardar_kpis_desigualdades(
                    nome_etapa,
                    codigo_componente,
                    nome_componente,
                )

                chave = chave_unidade(
                    "desigualdades",
                    nome_rede,
                    ano,
                    codigo_etapa,
                    codigo_componente,
                )
                if chave in unidades_concluidas:
                    print(
                        f"  Unidade já concluída; pulando "
                        f"{ano or 'geral'} | {nome_etapa} | "
                        f"{nome_componente}."
                    )
                    continue
                iniciar_unidade(
                    chave,
                    secao="desigualdades",
                    rede=nome_rede,
                    ano=ano,
                    etapa=nome_etapa,
                    componente=nome_componente,
                )
                registros_localidade = ler_cartoes_kpi(
                    driver,
                    titulo_secao,
                    ano,
                    nome_rede=nome_rede,
                    etapa=nome_etapa,
                    componente=nome_componente,
                )
                registros = filtrar_indicadores_municipais(
                    registros_localidade,
                    indicadores_municipais,
                )
                registros = filtrar_indicadores_selecionados(
                    "desigualdades",
                    registros,
                )
                print(
                    f"  {ano or 'geral'} | {nome_etapa} | "
                    f"{nome_componente}: {len(registros)} KPIs específicos"
                )
                dados_secao.extend(registros)
                dados_por_secao[titulo_secao] = (
                    pd.DataFrame(dados_secao).drop_duplicates()
                )
                verificar_parada()
                salvar_checkpoint()
                baixar_csvs_dos_graficos(
                    driver=driver,
                    diretorio_temporario=DIRETORIO_DOWNLOAD_TEMP,
                    diretorio_saida=DIRETORIO_CSV,
                    codigo_secao="desigualdades",
                    ano=ano,
                    rede=nome_rede,
                    contexto=f"{nome_etapa}_{nome_componente}",
                    tentativas=1,
                    timeout_download=2,
                    ignorar_canvas_sem_dados=True,
                    verificar_parada=verificar_parada,
                )
                concluir_unidade(chave)

    if dados_secao:
        dados_por_secao[titulo_secao] = (
            pd.DataFrame(dados_secao).drop_duplicates()
        )
        print(
            f"Seção concluída: {titulo_secao} | "
            f"{len(dados_por_secao[titulo_secao])} registros"
        )
    else:
        print(f"Nenhum indicador extraído de {titulo_secao}.")


retomar_automaticamente = False
execucao_concluida = False

try:
    verificar_parada()
    driver.get(URL)

    wait.until(
        lambda d: d.execute_script(
            "return document.readyState"
        ) == "complete"
    )

    # Abre a seção inicial, como no código anterior
    seletor_secao = (
        "#main-content > div > div > div:nth-child(2) > div:nth-child(1)"
    )

    secao_inicial = wait.until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, seletor_secao)
        )
    )

    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});",
        secao_inicial
    )

    secao_inicial.click()

    # Aguarda a navegação completa e usa o identificador estável da seção.
    wait.until(
        EC.presence_of_element_located((
            By.CSS_SELECTOR,
            'button.sidebar-tab[data-view="fluxo"]'
        ))
    )

    # Lê a lista de seções da barra lateral
    abas_sidebar = driver.find_elements(
        By.CSS_SELECTOR,
        "button.sidebar-tab[data-view]"
    )

    secoes = []

    for aba in abas_sidebar:
        codigo = aba.get_attribute("data-view")
        nome = aba.text.strip()

        if codigo:
            secoes.append({
                "codigo": codigo,
                "nome": nome
            })

    # Localiza IDEB como limite para incluir SAEB e IDEB no scraping.
    indice_fim = None

    for i, secao in enumerate(secoes):
        if secao["codigo"] == "ideb":
            indice_fim = i
            break

    if indice_fim is None:
        raise Exception(
            "A seção com data-view='ideb' não foi encontrada "
            "na barra lateral."
        )

    # Identifica a seção atualmente ativa
    indice_inicio = 0

    for i, secao in enumerate(secoes):
        aba = driver.find_element(
            By.CSS_SELECTOR,
            f'button.sidebar-tab[data-view="{secao["codigo"]}"]'
        )

        if "active" in (aba.get_attribute("class") or ""):
            indice_inicio = i
            break

    indices_secao = {
        secao["codigo"]: i
        for i, secao in enumerate(secoes)
    }
    secao_salva = estado_checkpoint.get("secao_inicial")
    if secao_salva:
        if secao_salva not in indices_secao:
            raise RuntimeError(
                f"A seção salva no checkpoint não existe: {secao_salva!r}. "
                "Use --descartar-checkpoint para iniciar do zero."
            )
        indice_inicio = indices_secao[secao_salva]
    elif argumentos_cli.iniciar_em:
        if argumentos_cli.iniciar_em not in indices_secao:
            codigos_disponiveis = ", ".join(indices_secao)
            raise ValueError(
                f"Seção inicial inválida: {argumentos_cli.iniciar_em!r}. "
                f"Códigos disponíveis: {codigos_disponiveis}."
            )
        indice_inicio = indices_secao[argumentos_cli.iniciar_em]

    # Percorre as seções até IDEB, inclusive.
    secoes_para_extrair = [
        secao
        for secao in secoes[indice_inicio:indice_fim + 1]
    ]
    if codigos_secao_selecionados:
        secoes_para_extrair = [
            secao
            for secao in secoes_para_extrair
            if secao["codigo"] in codigos_secao_selecionados
        ]
    if not secoes_para_extrair:
        raise ValueError(
            "Nenhuma seção selecionada está disponível para coleta."
        )
    if not estado_checkpoint.get("secao_inicial"):
        estado_checkpoint["secao_inicial"] = secoes[indice_inicio]["codigo"]

    if ARQUIVO_CHECKPOINT.exists():
        print(
            "Retomando desde a seção inicial salva: "
            f"{estado_checkpoint['secao_inicial']}"
        )
    salvar_checkpoint()

    print("\nSeções que serão extraídas:")

    for secao in secoes_para_extrair:
        print(f"- {secao['nome']}")

    for secao in secoes_para_extrair:

        verificar_parada()
        codigo_secao = secao["codigo"]
        nome_sidebar = secao["nome"]

        print("\n" + "=" * 50)
        print(f"Acessando seção: {nome_sidebar}")
        print("=" * 50)

        # Reinicia na rede Estadual antes da troca de seção e dos filtros.
        if driver.find_elements(By.ID, "rede-toggle"):
            selecionar_rede("estadual")

        seletor_aba = (
            f'button.sidebar-tab[data-view="{codigo_secao}"]'
        )

        aba = wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, seletor_aba)
            )
        )

        if "active" not in (aba.get_attribute("class") or ""):
            titulo_anterior = driver.execute_script(
                """
                const elemento = document.querySelector(
                    ".section-banner-left h2"
                );
                if (!elemento) return "";
                const texto = Array.from(elemento.childNodes)
                    .find(no => no.nodeType === Node.TEXT_NODE);
                return texto
                    ? texto.textContent.trim()
                    : elemento.textContent.trim();
                """
            )

            driver.execute_script(
                "arguments[0].click();",
                aba
            )

            wait.until(
                lambda d: d.execute_script(
                    """
                    const elemento = document.querySelector(
                        ".section-banner-left h2"
                    );
                    if (!elemento) return "";
                    const texto = Array.from(elemento.childNodes)
                        .find(no => no.nodeType === Node.TEXT_NODE);
                    return texto
                        ? texto.textContent.trim()
                        : elemento.textContent.trim();
                    """
                ) != titulo_anterior
            )

        wait.until(
            lambda d: "active" in (
                d.find_element(
                    By.CSS_SELECTOR,
                    seletor_aba
                ).get_attribute("class") or ""
            )
        )

        limpar_filtros_geograficos()

        if codigo_secao == "desigualdades":
            botoes_rede = driver.find_elements(
                By.CSS_SELECTOR,
                "#main-content #rede-toggle .rede-toggle-btn",
            )
            redes_desigualdades = [
                botao.get_attribute("data-rede")
                for botao in botoes_rede
                if botao.get_attribute("data-rede")
                and botao.is_enabled()
                and (
                    not codigos_rede_selecionados
                    or botao.get_attribute("data-rede")
                    in codigos_rede_selecionados
                )
            ]
            if not botoes_rede:
                extrair_desigualdades(nome_sidebar)
            elif not redes_desigualdades:
                print(
                    "Nenhuma das redes selecionadas está disponível em "
                    "Desigualdades; pulando seção."
                )
            else:
                for codigo_rede in redes_desigualdades:
                    verificar_parada()
                    selecionar_rede(codigo_rede)
                    extrair_desigualdades(nome_sidebar)
        elif codigo_secao == "saers":
            extrair_secao(
                nome_sidebar,
                codigo_secao,
                seletor_cartoes="#main-content #saers-kpi-cards .kpi-card"
            )
        else:
            # As demais seções continuam extraídas por rede e kpi-strip.
            extrair_secao(nome_sidebar, codigo_secao)

        # O laço termina após extrair IDEB.

    verificar_parada()
    salvar_dados_excel(dados_por_secao, NOME_ARQUIVO_EXCEL)

    total_registros = sum(
        len(df) for df in dados_por_secao.values()
    )

    print("\nExtração finalizada.")
    print(f"Total de seções: {len(dados_por_secao)}")
    print(f"Total de registros: {total_registros}")
    print(f"Arquivo gerado: {NOME_ARQUIVO_EXCEL}")

    print("\nAbas criadas:")

    for titulo_secao in dados_por_secao:
        print(f"- {titulo_secao}")

    gerenciador_checkpoint.remover()
    execucao_concluida = True
    print("Checkpoint removido após a conclusão.")

except ScrapingInterrompido:
    print("\nInterrupção solicitada. Salvando os dados coletados até aqui.")
    salvar_saida_parcial()
    print("Coleta interrompida. Os CSVs baixados e o checkpoint foram mantidos.")

except Exception:
    traceback.print_exc()
    tentativas = gerenciador_checkpoint.registrar_tentativa_retomada(
        unidades_concluidas,
        dados_por_secao,
    )
    if tentativas > MAX_TENTATIVAS_RETOMADA:
        print(
            "Limite de retomadas automáticas atingido. "
            "O checkpoint foi mantido para uma retomada manual."
        )
        raise

    print(
        f"Falha durante a coleta. Retomando automaticamente "
        f"({tentativas}/{MAX_TENTATIVAS_RETOMADA}) a partir do checkpoint."
    )
    retomar_automaticamente = True

finally:
    try:
        driver.quit()
    except WebDriverException as erro:
        print(f"Não foi possível encerrar o navegador corretamente: {erro}")

    if retomar_automaticamente:
        argumentos_retomada = sys.argv[1:]
        if "--descartar-checkpoint" in argumentos_retomada:
            argumentos_retomada = [
                argumento
                for argumento in argumentos_retomada
                if argumento != "--descartar-checkpoint"
            ]
        processo_retomada = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                *argumentos_retomada,
            ],
            cwd=str(Path.cwd()),
        )
        codigo_saida = processo_retomada.wait()
        if codigo_saida:
            raise SystemExit(codigo_saida)
    elif (
        execucao_concluida
        and not argumentos_cli.sem_pausa
    ):
        input("Pressione Enter para fechar o navegador...")
