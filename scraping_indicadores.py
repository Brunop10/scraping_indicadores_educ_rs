def filtrar_kpis_por_mudanca(dados_referencia, dados_filtrados, recorte):
    """Retorna KPIs cujo valor mudou após aplicar um recorte geográfico."""
    valores_referencia = {
        (registro.get("Indicador"), registro.get("Ano")): registro.get("Valor")
        for registro in dados_referencia
    }
    dados_com_mudanca = [
        registro
        for registro in dados_filtrados
        if valores_referencia.get(
            (registro.get("Indicador"), registro.get("Ano")),
            object(),
        ) != registro.get("Valor")
    ]
    print(
        f"  KPIs que mudaram após o recorte {recorte}: "
        f"{len(dados_com_mudanca)} de {len(dados_filtrados)}"
    )
    return dados_com_mudanca


def filtrar_indicadores_municipais(dados, indicadores_municipais):
    return [
        registro
        for registro in dados
        if registro.get("Indicador") in indicadores_municipais
    ]


def normalizar_indicador(indicador, titulo_secao=None):
    """Mantém o nome completo dos indicadores da aba SAERS."""
    if indicador is None:
        return indicador

    indicador = " ".join(str(indicador).split()).strip()
    if not indicador:
        return indicador

    if titulo_secao and "saers" in titulo_secao.lower():
        prefixo = "Proficiência Média por Etapa"
        indicador_min = indicador.lower()
        if indicador_min.startswith(prefixo.lower()):
            return indicador
        if "proficiência média por etapa" in indicador_min:
            return indicador
        return f"{prefixo} - {indicador}"

    return indicador


def ler_cartoes_kpi(
    driver,
    titulo_secao,
    ano,
    nome_rede=None,
    etapa=None,
    componente=None,
    seletor_cartoes="#main-content .kpi-strip .kpi-card",
):
    """Lê os cartões visíveis e retorna os registros dos indicadores."""
    cartoes = driver.execute_script(
        """
        return Array.from(document.querySelectorAll(arguments[0])).map(card => ({
            indicador: card.querySelector(".kpi-label")?.textContent.trim() || "",
            valor: card.querySelector(".kpi-value")?.textContent.trim() || "",
            variacao: card.querySelector(".kpi-delta")?.textContent.trim() || "",
            diferenca: card.querySelector(".kpi-abs")?.textContent.trim() || ""
        }));
        """,
        seletor_cartoes,
    )

    dados = []
    for cartao in cartoes:
        registro = {
            "Seção": titulo_secao,
            "Ano": ano,
        }

        if nome_rede is not None:
            registro["Rede"] = nome_rede
        registro["Indicador"] = normalizar_indicador(
            cartao["indicador"],
            titulo_secao,
        )
        registro["Valor"] = cartao["valor"]
        registro["Variação"] = " ".join(
            texto
            for texto in (cartao["variacao"], cartao["diferenca"])
            if texto
        )

        if etapa is not None:
            registro["Etapa"] = etapa
        if componente is not None:
            registro["Componente curricular"] = componente

        dados.append(registro)

    return dados
