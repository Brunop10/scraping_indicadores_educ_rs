import re

import pandas as pd


def salvar_dados_excel(dados_por_secao, nome_arquivo):
    with pd.ExcelWriter(nome_arquivo, engine="openpyxl") as writer:
        nomes_abas_usados = set()

        for titulo_secao, df_secao in dados_por_secao.items():
            nome_aba = re.sub(r'[\[\]:*?/\\]', "", titulo_secao).strip()
            nome_aba = nome_aba[:31] or "Indicadores"

            nome_original = nome_aba
            contador = 1
            while nome_aba in nomes_abas_usados:
                sufixo = f"_{contador}"
                nome_aba = nome_original[:31 - len(sufixo)] + sufixo
                contador += 1

            nomes_abas_usados.add(nome_aba)
            df_secao.to_excel(writer, sheet_name=nome_aba, index=False)
