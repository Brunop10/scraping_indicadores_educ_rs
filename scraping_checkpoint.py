import json
from pathlib import Path


class GerenciadorCheckpoint:
    def __init__(self, arquivo, secao_inicial=None, descartar=False):
        self.arquivo = Path(arquivo)

        if descartar and self.arquivo.exists():
            self.arquivo.unlink()
            print("Checkpoint anterior descartado.")

        if self.arquivo.exists():
            self.estado = json.loads(
                self.arquivo.read_text(encoding="utf-8")
            )
            if self.estado.get("versao") in (1, 2, 3):
                print(
                    "Migrando checkpoint: os KPIs serão reprocessados com a nova "
                    "comparação por aba; os CSVs existentes serão preservados."
                )
                self.estado.update({
                    "versao": 4,
                    "unidade_atual": None,
                    "unidades_concluidas": [],
                    "dados_por_secao": {},
                    "tentativas_retomada": 0,
                })
            elif self.estado.get("versao") != 4:
                raise RuntimeError(
                    "Versão de checkpoint incompatível. Use --descartar-checkpoint."
                )
            print("Checkpoint encontrado; a coleta será retomada automaticamente.")
            self.estado.setdefault("tentativas_retomada", 0)
        else:
            self.estado = {
                "versao": 4,
                "secao_inicial": secao_inicial,
                "unidade_atual": None,
                "unidades_concluidas": [],
                "dados_por_secao": {},
                "tentativas_retomada": 0,
            }

    def salvar(self, unidades_concluidas, dados_por_secao):
        self.estado["unidades_concluidas"] = sorted(unidades_concluidas)
        self.estado["dados_por_secao"] = {
            titulo: df.to_dict(orient="records")
            for titulo, df in dados_por_secao.items()
        }
        arquivo_temporario = self.arquivo.with_suffix(".tmp")
        arquivo_temporario.write_text(
            json.dumps(self.estado, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        arquivo_temporario.replace(self.arquivo)

    def iniciar_unidade(self, chave, unidades_concluidas, dados_por_secao, **contexto):
        self.estado["unidade_atual"] = {
            "chave": chave,
            **contexto,
        }
        self.salvar(unidades_concluidas, dados_por_secao)

    def concluir_unidade(self, chave, unidades_concluidas, dados_por_secao):
        unidades_concluidas.add(chave)
        self.estado["unidade_atual"] = None
        self.estado["tentativas_retomada"] = 0
        self.salvar(unidades_concluidas, dados_por_secao)

    def registrar_tentativa_retomada(self, unidades_concluidas, dados_por_secao):
        tentativas = self.estado.get("tentativas_retomada", 0) + 1
        self.estado["tentativas_retomada"] = tentativas
        self.salvar(unidades_concluidas, dados_por_secao)
        return tentativas

    def remover(self):
        self.arquivo.unlink(missing_ok=True)
