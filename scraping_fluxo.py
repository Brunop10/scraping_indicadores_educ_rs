def selecionar_valor_filtro(driver, select_id, valor):
    """Atualiza um select possivelmente oculto e dispara o evento change."""
    atualizado = driver.execute_script(
        """
        const select = document.getElementById(arguments[0]);
        const valor = String(arguments[1]);
        if (!select || !Array.from(select.options).some(
            option => option.value === valor
        )) return false;
        if (select.value !== valor) {
            select.value = valor;
            select.dispatchEvent(new Event("change", { bubbles: true }));
        }
        return select.value === valor;
        """,
        select_id,
        valor,
    )
    if not atualizado:
        raise RuntimeError(
            f"Não foi possível selecionar {valor!r} em #{select_id}."
        )


def selecionar_pill_fluxo(driver, wait, seletor_grupo, atributo, valor, exclusivo=False):
    """Ativa um pill do fluxo e confirma o estado no painel."""
    estado_pill = driver.execute_script(
        """
        const grupo = document.querySelector(arguments[0]);
        const botoes = Array.from(
            grupo?.querySelectorAll(".flx-pill") || []
        );
        const alvo = botoes.find(
            botao => botao.getAttribute(arguments[1]) === arguments[2]
        );
        if (!alvo || alvo.disabled) return "indisponivel";
        if (!alvo.classList.contains("active")) alvo.click();
        return "selecionado";
        """,
        seletor_grupo,
        atributo,
        valor,
    )
    if estado_pill == "indisponivel":
        return False

    if estado_pill != "selecionado":
        raise RuntimeError(
            f"Estado inesperado ao selecionar o pill {valor!r} "
            f"em {seletor_grupo}: {estado_pill!r}."
        )

    wait.until(
        lambda d: d.execute_script(
            """
            const grupo = document.querySelector(arguments[0]);
            const botoes = Array.from(
                grupo?.querySelectorAll(".flx-pill") || []
            );
            const ativos = botoes.filter(
                botao => botao.classList.contains("active")
            );
            return ativos.some(
                botao => botao.getAttribute(arguments[1]) === arguments[2]
            ) && (
                !arguments[3]
                || (
                    ativos.length === 1
                    && ativos[0].getAttribute(arguments[1]) === arguments[2]
                )
            );
            """,
            seletor_grupo,
            atributo,
            valor,
            exclusivo,
        )
    )
    return True


def grafico_fluxo_tem_dados(driver, canvas_id):
    """Confirma que o gráfico está renderizado com valores utilizáveis."""
    return driver.execute_script(
        """
        const canvas = document.getElementById(arguments[0]);
        const chart = canvas && window.Chart?.getChart(canvas);
        return Boolean(chart && chart.data.datasets.some(dataset =>
            Array.isArray(dataset.data)
            && dataset.data.some(
                valor => valor !== null && valor !== undefined
            )
        ));
        """,
        canvas_id,
    )


def baixar_csvs_fluxo_rendimento(
    driver,
    wait,
    diretorio_temporario,
    diretorio_saida,
    baixar_csvs_dos_graficos,
    ano,
    rede,
    verificar_parada=None,
):
    """Baixa os gráficos de fluxo para cada etapa e taxa por série."""
    argumentos_download = {
        "driver": driver,
        "diretorio_temporario": diretorio_temporario,
        "diretorio_saida": diretorio_saida,
        "codigo_secao": "fluxo",
        "ano": ano,
        "rede": rede,
    }

    for etapa in ("aprov_fund_ai", "aprov_fund_af", "aprov_med"):
        if verificar_parada:
            verificar_parada()
        selecionar_pill_fluxo(
            driver,
            wait,
            "#flx-aprov-pills",
            "data-key",
            etapa,
        )

    baixar_csvs_dos_graficos(
        **argumentos_download,
        ids_graficos=("flx-chart-aprov",),
        sobrescrever=True,
        verificar_parada=verificar_parada,
    )

    if not grafico_fluxo_tem_dados(driver, "flx-chart-serie"):
        print(
            "  Gráfico Taxa por Série indisponível ou sem dados; "
            "pulando as exportações por taxa."
        )
        return

    taxas_por_serie = (
        ("aprov", "aprovacao"),
        ("reprov", "reprovacao"),
        ("aband", "abandono"),
    )
    for taxa, contexto in taxas_por_serie:
        if verificar_parada:
            verificar_parada()
        pill_selecionado = selecionar_pill_fluxo(
            driver,
            wait,
            "#flx-serie-pills",
            "data-rate",
            taxa,
            exclusivo=True,
        )
        if not pill_selecionado:
            print(
                f"  Pill da taxa {taxa} indisponível; "
                "pulando esta exportação."
            )
            continue

        baixar_csvs_dos_graficos(
            **argumentos_download,
            contexto=f"taxa_por_serie_{contexto}",
            ids_graficos=("flx-chart-serie",),
            verificar_parada=verificar_parada,
        )
