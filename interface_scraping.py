import json
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import messagebox, ttk

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait


URL = "https://indicadores.educacao.rs.gov.br/"
DIRETORIO_PROJETO = Path(__file__).resolve().parent
ARQUIVO_SCRAPER = DIRETORIO_PROJETO / "scraping_selenium.py"


class ListaSelecao(ttk.Frame):
    def __init__(self, pai, ao_alterar=None):
        super().__init__(pai)
        self.ao_alterar = ao_alterar
        self.opcoes = []

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        barra = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
        )
        barra.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=barra.set)

        self.conteudo = ttk.Frame(self.canvas)
        self.janela_conteudo = self.canvas.create_window(
            (0, 0),
            window=self.conteudo,
            anchor="nw",
        )
        self.conteudo.columnconfigure(0, weight=1)
        self.conteudo.bind(
            "<Configure>",
            lambda evento: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            ),
        )
        self.canvas.bind(
            "<Configure>",
            lambda evento: self.canvas.itemconfigure(
                self.janela_conteudo,
                width=evento.width,
            ),
        )

    def configurar_opcoes(self, opcoes, selecionadas=True):
        for filho in self.conteudo.winfo_children():
            filho.destroy()
        self.opcoes = []
        for indice, texto in enumerate(opcoes):
            variavel = tk.BooleanVar(master=self, value=selecionadas)
            caixa = ttk.Checkbutton(
                self.conteudo,
                text=texto,
                variable=variavel,
                command=self._ao_alterar,
            )
            caixa.grid(
                row=indice,
                column=0,
                sticky="ew",
                padx=8,
                pady=3,
            )
            self.opcoes.append((variavel, caixa))

    def configurar_estado(self, estado):
        for _, caixa in self.opcoes:
            caixa.configure(state=estado)

    def indices_selecionados(self):
        return tuple(
            indice
            for indice, (variavel, _) in enumerate(self.opcoes)
            if variavel.get()
        )

    def selecionar_todos(self, selecionados):
        for variavel, _ in self.opcoes:
            variavel.set(selecionados)
        self._ao_alterar()

    def _ao_alterar(self):
        if self.ao_alterar:
            self.ao_alterar()


class InterfaceScraping:
    def __init__(self, janela):
        self.janela = janela
        self.janela.title("Scraping — Indicadores Educacionais RS")
        self.janela.geometry("900x760")
        self.janela.minsize(760, 620)
        self.janela.protocol("WM_DELETE_WINDOW", self.fechar)

        self.opcoes_cre = []
        self.cre_por_nome = {}
        self.municipios = []
        self.secoes = []
        self.redes = []
        self.carregando_opcoes = False
        self.descobrindo_opcoes = False
        self.scraping_em_andamento = False
        self.parada_solicitada = False
        self.arquivo_configuracao = None
        self.arquivo_parada = None

        self._montar_interface()
        self.janela.after(100, self.carregar_opcoes_iniciais)

    def _montar_interface(self):
        self.janela.columnconfigure(0, weight=1)
        self.janela.rowconfigure(3, weight=1)

        ttk.Label(
            self.janela,
            text="Indicadores Educacionais RS",
            font=("Segoe UI", 18, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(16, 2))
        ttk.Label(
            self.janela,
            text="Escolha a localidade, as seções e as redes que deseja coletar.",
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 12))

        localidade = ttk.LabelFrame(
            self.janela,
            text="Localidade",
            padding=12,
        )
        localidade.grid(row=2, column=0, sticky="ew", padx=20)
        localidade.columnconfigure(1, weight=1)

        ttk.Label(localidade, text="CRE").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 8)
        )
        self.cre_combo = ttk.Combobox(localidade, state="disabled")
        self.cre_combo.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        self.cre_combo.bind("<<ComboboxSelected>>", self.ao_selecionar_cre)

        ttk.Label(localidade, text="Município").grid(
            row=1, column=0, sticky="w", padx=(0, 10)
        )
        self.municipio_combo = ttk.Combobox(localidade, state="disabled")
        self.municipio_combo.grid(row=1, column=1, sticky="ew")
        self.municipio_combo.bind(
            "<<ComboboxSelected>>",
            self.ao_selecionar_municipio,
        )

        selecoes = ttk.Frame(self.janela, padding=(20, 12, 20, 8))
        selecoes.grid(row=3, column=0, sticky="nsew")
        selecoes.columnconfigure(0, weight=1)
        selecoes.columnconfigure(1, weight=1)
        selecoes.rowconfigure(1, weight=1)

        ttk.Label(selecoes, text="Seções").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=(0, 5)
        )
        ttk.Label(selecoes, text="Redes").grid(
            row=0, column=1, sticky="w", pady=(0, 5)
        )

        self.lista_secoes = self._criar_lista(
            selecoes,
            coluna=0,
            padx_direita=10,
        )
        self.lista_redes = self._criar_lista(selecoes, coluna=1)

        self.botoes_secoes = self._criar_botoes_selecao(
            selecoes,
            coluna=0,
            lista=self.lista_secoes,
        )
        self.botoes_redes = self._criar_botoes_selecao(
            selecoes,
            coluna=1,
            lista=self.lista_redes,
        )
        self.status = tk.StringVar(value="Carregando CREs disponíveis no site...")
        ttk.Label(
            self.janela,
            textvariable=self.status,
            wraplength=850,
        ).grid(row=4, column=0, sticky="w", padx=20, pady=(0, 8))

        botoes = ttk.Frame(self.janela)
        botoes.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 8))
        self.botao_scraping = ttk.Button(
            botoes,
            text="Scraping",
            command=self.iniciar_scraping,
            state="disabled",
        )
        self.botao_scraping.pack(side="left")
        self.botao_parar = ttk.Button(
            botoes,
            text="Parar e salvar parcial",
            command=self.parar_scraping,
            state="disabled",
        )
        self.botao_parar.pack(side="left", padx=(8, 0))

        area_log = ttk.LabelFrame(
            self.janela,
            text="Andamento",
            padding=8,
        )
        area_log.grid(
            row=6,
            column=0,
            sticky="nsew",
            padx=20,
            pady=(0, 14),
        )
        self.janela.rowconfigure(6, weight=1)
        area_log.columnconfigure(0, weight=1)
        area_log.rowconfigure(0, weight=1)
        self.log = tk.Text(area_log, height=8, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        barra = ttk.Scrollbar(area_log, command=self.log.yview)
        barra.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=barra.set)

    def _criar_lista(self, pai, coluna, padx_direita=0):
        lista = ListaSelecao(pai, self._atualizar_estado_campos)
        lista.grid(
            row=1,
            column=coluna,
            sticky="nsew",
            padx=(0, padx_direita),
        )
        return lista

    def _criar_botoes_selecao(self, pai, coluna, lista):
        quadro = ttk.Frame(pai)
        quadro.grid(row=2, column=coluna, sticky="w", pady=(8, 0))
        botoes = [
            ttk.Button(
                quadro,
                text="Todas",
                command=lambda: lista.selecionar_todos(True),
            ),
            ttk.Button(
                quadro,
                text="Nenhuma",
                command=lambda: lista.selecionar_todos(False),
            ),
        ]
        botoes[0].pack(side="left")
        botoes[1].pack(side="left", padx=(5, 0))
        return botoes

    def _buscar_opcoes_no_site(self, cre_id=None, municipio=None):
        opcoes_chrome = webdriver.ChromeOptions()
        opcoes_chrome.add_argument("--headless=new")
        opcoes_chrome.page_load_strategy = "eager"
        navegador = webdriver.Chrome(options=opcoes_chrome)

        try:
            espera = WebDriverWait(navegador, 45)
            navegador.get(URL)
            card_acesso = espera.until(
                lambda driver: driver.find_element(
                    By.CSS_SELECTOR,
                    '[data-nav="acesso"]',
                )
            )
            navegador.execute_script(
                "arguments[0].click();",
                card_acesso,
            )

            campo_cre = espera.until(
                lambda driver: driver.find_element(By.ID, "sel-cre")
            )
            opcoes_cre = [
                (opcao.get_attribute("value"), opcao.text.strip())
                for opcao in Select(campo_cre).options
                if opcao.get_attribute("value")
            ]
            if not opcoes_cre:
                raise RuntimeError("O site não retornou nenhuma CRE disponível.")

            if cre_id is None:
                cre_id = opcoes_cre[0][0]
            Select(campo_cre).select_by_value(cre_id)

            campo_municipio = espera.until(
                lambda driver: driver.find_element(
                    By.ID,
                    "mun-search-input",
                )
            )
            campo_municipio.click()
            navegador.execute_script(
                """
                arguments[0].value = "";
                arguments[0].dispatchEvent(
                    new Event("input", { bubbles: true })
                );
                """,
                campo_municipio,
            )
            espera.until(
                lambda driver: driver.find_elements(
                    By.CSS_SELECTOR,
                    "#mun-dropdown-list .mun-dd-item[data-value]",
                )
            )
            municipios = [
                item.text.strip()
                for item in navegador.find_elements(
                    By.CSS_SELECTOR,
                    "#mun-dropdown-list .mun-dd-item[data-value]",
                )
                if item.get_attribute("data-value") and item.text.strip()
            ]
            resultado = {"cres": opcoes_cre, "municipios": municipios}
            if municipio is None:
                return resultado
            if municipio not in municipios:
                raise ValueError(
                    f"{municipio!r} não está disponível na CRE selecionada."
                )

            municipio_selecionado = navegador.execute_script(
                """
                const nome = arguments[0].toLocaleLowerCase();
                const opcao = Array.from(document.querySelectorAll(
                    "#mun-dropdown-list .mun-dd-item"
                )).find(item =>
                    item.textContent.trim().toLocaleLowerCase() === nome
                );
                if (!opcao) return false;
                opcao.click();
                return true;
                """,
                municipio,
            )
            if not municipio_selecionado:
                raise RuntimeError(
                    f"Não foi possível aplicar o filtro de {municipio}."
                )
            espera.until(
                lambda driver: driver.execute_script(
                    """
                    return document.querySelector("#mun-filter-slot")
                        ?.innerText.includes(arguments[0]) || false;
                    """,
                    municipio,
                )
            )

            codigos_disponiveis = [
                codigo
                for codigo in navegador.execute_script(
                    """
                    return Array.from(document.querySelectorAll(
                        "button.sidebar-tab[data-view]"
                    )).map(item => item.getAttribute("data-view"));
                    """
                )
                if codigo
            ]
            if "ideb" not in codigos_disponiveis:
                raise RuntimeError("A seção IDEB não foi encontrada no site.")
            codigos_secoes = codigos_disponiveis[
                :codigos_disponiveis.index("ideb") + 1
            ]
            codigos_secoes = [
                codigo for codigo in codigos_secoes if codigo != "home"
            ]

            secoes = []
            redes_por_codigo = {}
            for codigo in codigos_secoes:
                espera.until(
                    lambda driver, codigo=codigo: driver.find_elements(
                        By.CSS_SELECTOR,
                        f'button.sidebar-tab[data-view="{codigo}"]',
                    )
                )
                seletor_aba = (
                    f'button.sidebar-tab[data-view="{codigo}"]'
                )
                aba = navegador.find_element(By.CSS_SELECTOR, seletor_aba)
                nome_secao = aba.get_attribute("textContent").strip()
                if "active" not in (aba.get_attribute("class") or ""):
                    navegador.execute_script(
                        "arguments[0].click();",
                        aba,
                    )
                    espera.until(
                        lambda driver, seletor_aba=seletor_aba: (
                            "active"
                            in (
                                driver.find_element(
                                    By.CSS_SELECTOR,
                                    seletor_aba,
                                ).get_attribute("class")
                                or ""
                            )
                        )
                    )

                nome_secao = navegador.execute_script(
                    """
                    const aba = document.querySelector(arguments[0]);
                    const textoAba = aba?.textContent.trim() || "";
                    if (textoAba) return textoAba;
                    const titulo = document.querySelector(
                        ".section-banner-left h2"
                    );
                    if (!titulo) return arguments[1];
                    const texto = Array.from(titulo.childNodes)
                        .find(no => no.nodeType === Node.TEXT_NODE);
                    return (
                        texto ? texto.textContent : titulo.textContent
                    ).trim() || arguments[1];
                    """,
                    seletor_aba,
                    codigo,
                )
                secoes.append({"codigo": codigo, "nome": nome_secao})

                redes = navegador.execute_script(
                    """
                    return Array.from(document.querySelectorAll(
                        "#main-content #rede-toggle .rede-toggle-btn"
                    )).map(botao => ({
                        codigo: botao.getAttribute("data-rede"),
                        nome: botao.textContent.trim()
                    })).filter(rede => rede.codigo && rede.nome);
                    """
                )
                for rede in redes:
                    redes_por_codigo.setdefault(
                        rede["codigo"],
                        rede["nome"],
                    )

            if not secoes:
                raise RuntimeError("O site não retornou seções para coleta.")
            if not redes_por_codigo:
                raise RuntimeError(
                    "O site não retornou redes para as seções disponíveis."
                )
            resultado.update({
                "secoes": secoes,
                "redes": [
                    {"codigo": codigo, "nome": nome}
                    for codigo, nome in redes_por_codigo.items()
                ],
            })
            return resultado
        finally:
            navegador.quit()

    def _executar_carregamento(self, funcao, argumentos, callback):
        try:
            resultado = funcao(*argumentos)
        except Exception as erro:
            self.janela.after(
                0,
                lambda erro=erro: callback(None, erro),
            )
        else:
            self.janela.after(
                0,
                lambda resultado=resultado: callback(resultado, None),
            )

    def carregar_opcoes_iniciais(self):
        self._carregar_opcoes_geograficas()

    def _carregar_opcoes_geograficas(self, cre_id=None):
        if self.carregando_opcoes or self.scraping_em_andamento:
            return
        self.carregando_opcoes = True
        self.cre_id_carregamento = cre_id
        self._atualizar_estado_campos()
        self.status.set("Carregando CREs e municípios no site...")
        threading.Thread(
            target=self._executar_carregamento,
            args=(
                self._buscar_opcoes_no_site,
                (cre_id, None),
                self._aplicar_opcoes_geograficas,
            ),
            daemon=True,
        ).start()

    def _aplicar_opcoes_geograficas(self, resultado, erro):
        self.carregando_opcoes = False
        if erro:
            self.municipios = []
            self.municipio_combo.set("")
            self.municipio_combo.configure(values=[])
            self.status.set("Não foi possível carregar as opções do site.")
            messagebox.showerror(
                "Falha ao carregar opções",
                f"Não foi possível obter CREs e municípios.\n\n{erro}",
                parent=self.janela,
            )
            self._atualizar_estado_campos()
            return

        self.opcoes_cre = resultado["cres"]
        self.cre_por_nome = {
            nome: codigo for codigo, nome in self.opcoes_cre
        }
        self.cre_combo.configure(
            state="readonly",
            values=[nome for _, nome in self.opcoes_cre],
        )
        nome_cre = next(
            (
                nome
                for codigo, nome in self.opcoes_cre
                if codigo == self.cre_id_carregamento
            ),
            self.opcoes_cre[0][1],
        )
        self.cre_combo.set(nome_cre)
        self.municipios = resultado["municipios"]
        self.municipio_combo.configure(
            state="readonly",
            values=self.municipios,
        )
        self.municipio_combo.set("")
        self.status.set("Selecione um município para carregar as seções e redes.")
        self._atualizar_estado_campos()

    def ao_selecionar_cre(self, evento):
        if evento.widget is not self.cre_combo:
            return
        codigo = self.cre_por_nome.get(self.cre_combo.get())
        if codigo:
            self.municipio_combo.set("")
            self._limpar_opcoes_scraping()
            self._carregar_opcoes_geograficas(codigo)

    def ao_selecionar_municipio(self, evento):
        if evento.widget is not self.municipio_combo:
            return
        cre_id = self.cre_por_nome.get(self.cre_combo.get())
        municipio = self.municipio_combo.get()
        if cre_id and municipio:
            self.descobrindo_opcoes = True
            self._limpar_opcoes_scraping()
            self._atualizar_estado_campos()
            self.status.set(
                "Carregando seções e redes disponíveis para a localidade..."
            )
            threading.Thread(
                target=self._executar_carregamento,
                args=(
                    self._buscar_opcoes_no_site,
                    (cre_id, municipio),
                    self._aplicar_opcoes_scraping,
                ),
                daemon=True,
            ).start()

    def _aplicar_opcoes_scraping(self, resultado, erro):
        self.descobrindo_opcoes = False
        if erro:
            self._limpar_opcoes_scraping()
            self.status.set("Não foi possível carregar seções e redes.")
            messagebox.showerror(
                "Falha ao carregar seções e redes",
                f"Não foi possível obter as opções do site.\n\n{erro}",
                parent=self.janela,
            )
            self._atualizar_estado_campos()
            return

        self.secoes = resultado["secoes"]
        self.redes = resultado["redes"]
        self.lista_secoes.configurar_opcoes(
            [secao["nome"] for secao in self.secoes]
        )
        self.lista_redes.configurar_opcoes(
            [rede["nome"] for rede in self.redes]
        )

        self.status.set(
            f"Selecione as opções: {len(self.secoes)} seções e "
            f"{len(self.redes)} redes carregadas."
        )
        self._atualizar_estado_campos()

    def _limpar_opcoes_scraping(self):
        self.secoes = []
        self.redes = []
        self.lista_secoes.configurar_opcoes([])
        self.lista_redes.configurar_opcoes([])

    def _atualizar_estado_campos(self):
        ocupado = (
            self.carregando_opcoes
            or self.descobrindo_opcoes
            or self.scraping_em_andamento
        )
        self.cre_combo.configure(
            state="disabled" if ocupado else (
                "readonly" if self.opcoes_cre else "disabled"
            )
        )
        self.municipio_combo.configure(
            state="disabled" if ocupado else (
                "readonly" if self.municipios else "disabled"
            )
        )
        estado_lista = "disabled" if ocupado else "normal"
        self.lista_secoes.configurar_estado(estado_lista)
        self.lista_redes.configurar_estado(estado_lista)
        estado_botoes = "disabled" if ocupado else "normal"
        for botao in self.botoes_secoes + self.botoes_redes:
            botao.configure(state=estado_botoes)
        habilitado = (
            not ocupado
            and bool(self.secoes)
            and bool(self.redes)
            and bool(self.lista_secoes.indices_selecionados())
            and bool(self.lista_redes.indices_selecionados())
            and bool(self.municipio_combo.get())
        )
        self.botao_scraping.configure(
            state="normal" if habilitado else "disabled"
        )

    def _adicionar_log(self, linha):
        self.log.configure(state="normal")
        self.log.insert("end", linha + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def iniciar_scraping(self):
        indices_secoes = self.lista_secoes.indices_selecionados()
        indices_redes = self.lista_redes.indices_selecionados()
        if not indices_secoes or not indices_redes:
            messagebox.showwarning(
                "Seleção incompleta",
                "Selecione ao menos uma seção e uma rede.",
                parent=self.janela,
            )
            return

        secoes_escolhidas = [
            self.secoes[indice] for indice in indices_secoes
        ]
        redes_escolhidas = [
            self.redes[indice] for indice in indices_redes
        ]
        configuracao = {
            "secoes": [secao["codigo"] for secao in secoes_escolhidas],
            "redes": [rede["codigo"] for rede in redes_escolhidas],
        }
        self.arquivo_configuracao = (
            Path(tempfile.gettempdir())
            / f"config_scraping_{uuid.uuid4().hex}.json"
        )
        self.arquivo_configuracao.write_text(
            json.dumps(configuracao, ensure_ascii=False),
            encoding="utf-8",
        )
        self.arquivo_parada = (
            Path(tempfile.gettempdir())
            / f"parar_scraping_{uuid.uuid4().hex}.flag"
        )
        self.parada_solicitada = False
        self.scraping_em_andamento = True
        self._atualizar_estado_campos()
        self.botao_parar.configure(state="normal")

        nome_cre = self.cre_combo.get()
        cre_id = self.cre_por_nome[nome_cre]
        municipio = self.municipio_combo.get()
        self.status.set(f"Coletando {municipio} — {nome_cre}...")
        self._adicionar_log(
            f"Scraping iniciado: {municipio} — {nome_cre}; "
            f"{len(secoes_escolhidas)} seções e {len(redes_escolhidas)} redes."
        )
        argumentos = [
            sys.executable,
            "-u",
            str(ARQUIVO_SCRAPER),
            "--cre-id",
            cre_id,
            "--cre-nome",
            nome_cre,
            "--municipio",
            municipio,
            "--config-json",
            str(self.arquivo_configuracao),
            "--arquivo-parada",
            str(self.arquivo_parada),
            "--sem-pausa",
        ]
        threading.Thread(
            target=self._executar_scraper,
            args=(argumentos,),
            daemon=True,
        ).start()

    def parar_scraping(self):
        if not self.scraping_em_andamento or self.parada_solicitada:
            return
        self.parada_solicitada = True
        if self.arquivo_parada:
            self.arquivo_parada.touch()
        self.botao_parar.configure(state="disabled")
        self.status.set("Parada solicitada; salvando o progresso com segurança...")
        self._adicionar_log(
            "Parada solicitada. A coleta será interrompida e os dados salvos."
        )

    def _executar_scraper(self, argumentos):
        try:
            processo = subprocess.Popen(
                argumentos,
                cwd=str(DIRETORIO_PROJETO),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            if processo.stdout is None:
                raise RuntimeError("Não foi possível ler a saída do scraper.")
            for linha in processo.stdout:
                self.janela.after(
                    0,
                    self._adicionar_log,
                    linha.rstrip(),
                )
            codigo_saida = processo.wait()
        except Exception as erro:
            self.janela.after(
                0,
                lambda erro=erro: self._finalizar_scraping(None, erro),
            )
        else:
            self.janela.after(
                0,
                lambda codigo_saida=codigo_saida: self._finalizar_scraping(
                    codigo_saida,
                    None,
                ),
            )

    def _finalizar_scraping(self, codigo_saida, erro):
        self.scraping_em_andamento = False
        self.botao_parar.configure(state="disabled")
        self._atualizar_estado_campos()
        for arquivo in (self.arquivo_configuracao, self.arquivo_parada):
            if arquivo and arquivo.exists():
                arquivo.unlink()

        if erro is not None:
            self.status.set("Falha ao iniciar ou acompanhar o scraper.")
            self._adicionar_log(f"Erro: {erro}")
        elif self.parada_solicitada and codigo_saida == 0:
            self.status.set("Scraping interrompido; progresso salvo.")
            self._adicionar_log(
                "Scraping interrompido. Os dados coletados foram salvos."
            )
        elif codigo_saida == 0:
            self.status.set("Scraping concluído.")
            self._adicionar_log("Scraping concluído com sucesso.")
        else:
            self.status.set(
                f"Scraping terminou com erro (código {codigo_saida})."
            )
            self._adicionar_log(
                f"Scraping terminou com erro (código {codigo_saida})."
            )

    def fechar(self):
        if (
            self.scraping_em_andamento
            or self.carregando_opcoes
            or self.descobrindo_opcoes
        ):
            messagebox.showinfo(
                "Operação em andamento",
                "Aguarde a operação atual terminar antes de fechar a interface.",
                parent=self.janela,
            )
            return
        self.janela.destroy()


if __name__ == "__main__":
    janela = tk.Tk()
    InterfaceScraping(janela)
    janela.mainloop()
