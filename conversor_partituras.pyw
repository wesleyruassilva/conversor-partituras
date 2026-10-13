# -*- coding: utf-8 -*-
"""
Conversor de Partituras — PDF -> MusicXML
Interface amigável para o Audiveris (https://audiveris.github.io).

Requisitos: Python 3.9+ (com Tkinter, padrão no instalador do Windows)
            e Audiveris 5.x instalado.
Opcional:   pip install tkinterdnd2   (ativa arrastar-e-soltar)

Licença: AGPL-3.0 (mesma do Audiveris).
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NOME = "Conversor de Partituras"
EXTENSOES = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")
CONFIG = Path(os.environ.get("APPDATA", Path.home())) / "ConversorPartituras" / "config.json"

# Etapas do Audiveris, para mostrar progresso legível
ETAPAS = {
    "LOAD": "carregando página", "BINARY": "preparando imagem",
    "SCALE": "medindo pautas", "GRID": "encontrando pautas e compassos",
    "HEADERS": "lendo claves e armaduras", "BEAMS": "lendo barras",
    "HEADS": "lendo cabeças de nota", "STEMS": "lendo hastes",
    "TEXTS": "lendo textos e letras", "MEASURES": "montando compassos",
    "CHORDS": "montando acordes", "CURVES": "lendo ligaduras",
    "SYMBOLS": "lendo símbolos", "RHYTHMS": "calculando ritmos",
    "PAGE": "finalizando página",
}

# Idiomas para o reconhecimento de letras (códigos do Tesseract)
IDIOMAS = {
    "Português": "por+eng", "Inglês": "eng", "Latim": "lat+eng",
    "Italiano": "ita+eng", "Sem letra": "eng",
}

try:  # arrastar-e-soltar, se disponível
    from tkinterdnd2 import DND_FILES, TkinterDnD
    TEM_DND = True
except Exception:
    TEM_DND = False


# ---------------------------------------------------------------- núcleo
def carregar_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def salvar_config(cfg):
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def pasta_do_app():
    """Pasta do .exe (versão portátil) ou do script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def audiveris_embutido():
    exe = pasta_do_app() / "audiveris" / "Audiveris.exe"
    return str(exe) if exe.is_file() else None


def localizar_audiveris(cfg):
    """Usa o Audiveris embutido; senão, procura em locais comuns do Windows."""
    embutido = audiveris_embutido()
    if embutido:
        return embutido
    salvo = cfg.get("audiveris")
    if salvo and Path(salvo).is_file():
        return salvo
    candidatos = []
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LOCALAPPDATA"), r"C:\Program Files"):
        if not base:
            continue
        pasta = Path(base) / "Audiveris"
        candidatos += [pasta / "Audiveris.exe", pasta / "bin" / "Audiveris.bat",
                       pasta / "bin" / "audiveris.bat"]
    for c in candidatos:
        if c.is_file():
            return str(c)
    for nome in ("Audiveris", "audiveris"):
        achado = shutil.which(nome)
        if achado:
            return achado
    return None


def converter(audiveris, entrada, pasta_saida, ao_ler_linha=None, ao_iniciar=None,
              idioma=None):
    """
    Roda o Audiveris em modo batch e devolve (lista_de_mxl, n_avisos, log).
    Lança RuntimeError se nenhum MusicXML for gerado.
    """
    entrada = Path(entrada)
    pasta_saida = Path(pasta_saida)
    pasta_saida.mkdir(parents=True, exist_ok=True)
    antes = {p: p.stat().st_mtime for p in pasta_saida.rglob("*.mxl")}

    cmd = [audiveris, "-batch", "-transcribe", "-export", "-output", str(pasta_saida)]
    if idioma:
        cmd += ["-constant", f"org.audiveris.omr.text.Language.defaultSpecification={idioma}"]
    cmd += ["--", str(entrada)]

    # Versão portátil: dicionários de texto (OCR) ficam ao lado do Audiveris
    ambiente = dict(os.environ)
    tessdata = Path(audiveris).parent.parent / "tessdata"
    if tessdata.is_dir():
        ambiente["TESSDATA_PREFIX"] = str(tessdata)

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=flags, env=ambiente,
                            shell=audiveris.lower().endswith(".bat"))
    if ao_iniciar:
        ao_iniciar(proc)
    log, avisos = [], 0
    for bruto in proc.stdout:
        linha = bruto.decode("utf-8", errors="replace").rstrip()
        log.append(linha)
        if "WARN" in linha or "ERROR" in linha:
            avisos += 1
        if ao_ler_linha:
            ao_ler_linha(linha)
    proc.wait()

    gerados = sorted(p for p in pasta_saida.rglob("*.mxl")
                     if p.stem.startswith(entrada.stem)
                     and (p not in antes or p.stat().st_mtime > antes[p]))
    if not gerados:
        raise RuntimeError("O Audiveris não gerou nenhum MusicXML "
                           f"(código de saída {proc.returncode}).")
    return gerados, avisos, "\n".join(log)


def abrir(caminho):
    if sys.platform.startswith("win"):
        os.startfile(caminho)  # noqa: abre no programa padrão (ex.: MuseScore)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", caminho])
    else:
        subprocess.Popen(["xdg-open", caminho])


# ---------------------------------------------------------------- interface
class App:
    def __init__(self, raiz):
        self.raiz = raiz
        self.cfg = carregar_config()
        self.audiveris = localizar_audiveris(self.cfg)
        self.itens = {}          # id da linha -> dict(arquivo, saidas, log)
        self.fila = queue.Queue()
        self.processo = None
        self.cancelar = False
        self.rodando = False

        raiz.title(APP_NOME)
        raiz.geometry("760x560")
        raiz.minsize(620, 460)
        self._estilo()
        self._montar()
        self._atualizar_status_audiveris()
        raiz.after(100, self._processar_fila)

    def _estilo(self):
        st = ttk.Style()
        if "vista" in st.theme_names():
            st.theme_use("vista")
        st.configure("Titulo.TLabel", font=("Segoe UI", 16, "bold"))
        st.configure("Dica.TLabel", font=("Segoe UI", 9), foreground="#555")
        st.configure("Grande.TButton", font=("Segoe UI", 11, "bold"), padding=(16, 8))

    def _montar(self):
        f = ttk.Frame(self.raiz, padding=16)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="Converter partitura em MusicXML", style="Titulo.TLabel").pack(anchor="w")
        dica = ("1. Adicione os arquivos   2. Clique em Converter   3. Abra o resultado"
                + ("   •   Você também pode arrastar arquivos para a lista" if TEM_DND else ""))
        ttk.Label(f, text=dica, style="Dica.TLabel").pack(anchor="w", pady=(2, 12))

        # Lista de arquivos
        quadro = ttk.Frame(f)
        quadro.pack(fill="both", expand=True)
        self.lista = ttk.Treeview(quadro, columns=("arquivo", "status"), show="headings",
                                  selectmode="extended", height=8)
        self.lista.heading("arquivo", text="Arquivo")
        self.lista.heading("status", text="Situação")
        self.lista.column("arquivo", width=440)
        self.lista.column("status", width=240)
        rolagem = ttk.Scrollbar(quadro, orient="vertical", command=self.lista.yview)
        self.lista.configure(yscrollcommand=rolagem.set)
        self.lista.pack(side="left", fill="both", expand=True)
        rolagem.pack(side="right", fill="y")
        self.lista.bind("<Double-1>", lambda e: self._abrir_resultado())
        if TEM_DND:
            self.lista.drop_target_register(DND_FILES)
            self.lista.dnd_bind("<<Drop>>", self._soltar)

        # Botões da lista
        b = ttk.Frame(f)
        b.pack(fill="x", pady=8)
        self.bt_add = ttk.Button(b, text="➕ Adicionar arquivos", command=self._adicionar)
        self.bt_add.pack(side="left")
        self.bt_rem = ttk.Button(b, text="Remover", command=self._remover)
        self.bt_rem.pack(side="left", padx=6)
        ttk.Button(b, text="🎼 Abrir resultado", command=self._abrir_resultado).pack(side="right")
        ttk.Button(b, text="📁 Abrir pasta", command=self._abrir_pasta).pack(side="right", padx=6)
        ttk.Button(b, text="Ver detalhes", command=self._ver_log).pack(side="right")

        # Pasta de saída
        s = ttk.LabelFrame(f, text="Onde salvar", padding=8)
        s.pack(fill="x", pady=(4, 8))
        self.modo_saida = tk.StringVar(value=self.cfg.get("modo_saida", "mesma"))
        self.pasta_saida = tk.StringVar(value=self.cfg.get("pasta_saida", ""))
        ttk.Radiobutton(s, text="Na mesma pasta do PDF", value="mesma",
                        variable=self.modo_saida).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(s, text="Nesta pasta:", value="fixa",
                        variable=self.modo_saida).grid(row=1, column=0, sticky="w")
        ttk.Entry(s, textvariable=self.pasta_saida).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(s, text="Escolher…", command=self._escolher_pasta).grid(row=1, column=2)
        s.columnconfigure(1, weight=1)

        # Idioma das letras / textos
        lin = ttk.Frame(f)
        lin.pack(fill="x", pady=(0, 8))
        ttk.Label(lin, text="Idioma das letras:").pack(side="left")
        self.idioma = tk.StringVar(value=self.cfg.get("idioma", "Português"))
        ttk.Combobox(lin, textvariable=self.idioma, values=list(IDIOMAS),
                     state="readonly", width=16).pack(side="left", padx=6)

        # Progresso e ação
        self.progresso = ttk.Progressbar(f, mode="determinate")
        self.progresso.pack(fill="x")
        self.msg = tk.StringVar(value="Pronto.")
        ttk.Label(f, textvariable=self.msg).pack(anchor="w", pady=(4, 8))

        rodape = ttk.Frame(f)
        rodape.pack(fill="x")
        self.aud_msg = tk.StringVar()
        ttk.Label(rodape, textvariable=self.aud_msg, style="Dica.TLabel").pack(side="left")
        ttk.Button(rodape, text="Localizar Audiveris…",
                   command=self._localizar_manual).pack(side="left", padx=6)
        self.bt_conv = ttk.Button(rodape, text="Converter", style="Grande.TButton",
                                  command=self._iniciar_ou_cancelar)
        self.bt_conv.pack(side="right")

    # ----- ações
    def _atualizar_status_audiveris(self):
        if self.audiveris and self.audiveris == audiveris_embutido():
            self.aud_msg.set("Audiveris: embutido ✔")
        elif self.audiveris:
            self.aud_msg.set("Audiveris: encontrado ✔")
        else:
            self.aud_msg.set("Audiveris: não encontrado ✖")

    def _localizar_manual(self):
        cam = filedialog.askopenfilename(
            title="Onde está o Audiveris?",
            filetypes=[("Audiveris", "Audiveris.exe *.bat *.exe"), ("Todos", "*.*")])
        if cam:
            self.audiveris = cam
            self.cfg["audiveris"] = cam
            salvar_config(self.cfg)
            self._atualizar_status_audiveris()

    def _incluir(self, caminhos):
        existentes = {d["arquivo"] for d in self.itens.values()}
        for c in caminhos:
            p = Path(c)
            if p.is_dir():
                self._incluir([x for x in p.iterdir() if x.suffix.lower() in EXTENSOES])
                continue
            if p.suffix.lower() in EXTENSOES and str(p) not in existentes:
                iid = self.lista.insert("", "end", values=(p.name, "Aguardando"))
                self.itens[iid] = {"arquivo": str(p), "saidas": [], "log": ""}

    def _adicionar(self):
        arqs = filedialog.askopenfilenames(
            title="Escolha as partituras",
            filetypes=[("Partituras", " ".join("*" + e for e in EXTENSOES)), ("Todos", "*.*")])
        self._incluir(arqs)

    def _soltar(self, evento):
        self._incluir(self.raiz.tk.splitlist(evento.data))

    def _remover(self):
        if self.rodando:
            return
        for iid in self.lista.selection():
            self.lista.delete(iid)
            self.itens.pop(iid, None)

    def _escolher_pasta(self):
        p = filedialog.askdirectory(title="Pasta para salvar os MusicXML")
        if p:
            self.pasta_saida.set(p)
            self.modo_saida.set("fixa")

    def _selecionado(self):
        sel = self.lista.selection() or self.lista.get_children()[:1]
        return sel[0] if sel else None

    def _abrir_resultado(self):
        iid = self._selecionado()
        if iid and self.itens[iid]["saidas"]:
            for s in self.itens[iid]["saidas"]:
                abrir(s)
        else:
            messagebox.showinfo(APP_NOME, "Este arquivo ainda não foi convertido.")

    def _abrir_pasta(self):
        iid = self._selecionado()
        if iid and self.itens[iid]["saidas"]:
            abrir(str(Path(self.itens[iid]["saidas"][0]).parent))
        elif iid:
            abrir(str(self._destino(self.itens[iid]["arquivo"])))

    def _ver_log(self):
        iid = self._selecionado()
        if not iid:
            return
        janela = tk.Toplevel(self.raiz)
        janela.title("Detalhes — " + Path(self.itens[iid]["arquivo"]).name)
        janela.geometry("720x420")
        t = tk.Text(janela, wrap="none", font=("Consolas", 9))
        t.insert("1.0", self.itens[iid]["log"] or "Sem detalhes ainda.")
        t.configure(state="disabled")
        t.pack(fill="both", expand=True)

    def _destino(self, arquivo):
        if self.modo_saida.get() == "fixa" and self.pasta_saida.get().strip():
            return Path(self.pasta_saida.get().strip())
        return Path(arquivo).parent

    def _iniciar_ou_cancelar(self):
        if self.rodando:
            self.cancelar = True
            if self.processo and self.processo.poll() is None:
                self.processo.kill()
            self.msg.set("Cancelando…")
            return
        if not self.audiveris:
            messagebox.showwarning(
                APP_NOME, "Não encontrei o Audiveris.\n\nInstale-o (veja o LEIA-ME) "
                "ou clique em “Localizar Audiveris…”.")
            return
        pendentes = [i for i in self.lista.get_children()
                     if not self.itens[i]["saidas"]]
        if not pendentes:
            messagebox.showinfo(APP_NOME, "Adicione ao menos um arquivo para converter.")
            return
        self.cfg.update(modo_saida=self.modo_saida.get(), pasta_saida=self.pasta_saida.get(),
                        idioma=self.idioma.get())
        salvar_config(self.cfg)
        self.rodando, self.cancelar = True, False
        self.bt_conv.configure(text="Cancelar")
        self.bt_add.state(["disabled"])
        self.bt_rem.state(["disabled"])
        self.progresso.configure(maximum=len(pendentes), value=0)
        # Lê as opções da janela aqui, antes de ir para o segundo plano
        idioma = IDIOMAS.get(self.idioma.get(), "eng")
        destinos = {i: self._destino(self.itens[i]["arquivo"]) for i in pendentes}
        threading.Thread(target=self._trabalhar, args=(pendentes, idioma, destinos),
                         daemon=True).start()

    def _trabalhar(self, pendentes, idioma, destinos):
        total = len(pendentes)
        for n, iid in enumerate(pendentes, 1):
            if self.cancelar:
                break
            arq = self.itens[iid]["arquivo"]
            nome = Path(arq).name
            self.fila.put(("status", iid, "Convertendo…"))
            self.fila.put(("msg", f"[{n}/{total}] {nome}: iniciando"))

            def ler(linha, nome=nome, n=n):
                for etapa, texto in ETAPAS.items():
                    if etapa in linha.split():
                        self.fila.put(("msg", f"[{n}/{total}] {nome}: {texto}…"))
                        break
            try:
                saidas, avisos, log = converter(
                    self.audiveris, arq, destinos[iid], ler,
                    ao_iniciar=lambda p: setattr(self, "processo", p), idioma=idioma)
                self.itens[iid].update(saidas=[str(s) for s in saidas], log=log)
                if avisos > 25:
                    st = f"✔ Pronto — revisar ({avisos} avisos)"
                else:
                    st = "✔ Pronto"
                if len(saidas) > 1:
                    st += f" — {len(saidas)} movimentos"
                self.fila.put(("status", iid, st))
            except Exception as e:
                if self.cancelar:
                    self.fila.put(("status", iid, "Cancelado"))
                else:
                    self.itens[iid]["log"] = str(e)
                    self.fila.put(("status", iid, "✖ Falhou — ver detalhes"))
            self.fila.put(("passo",))
        self.fila.put(("fim",))

    def _processar_fila(self):
        try:
            while True:
                ev = self.fila.get_nowait()
                if ev[0] == "status":
                    self.lista.set(ev[1], "status", ev[2])
                elif ev[0] == "msg":
                    self.msg.set(ev[1])
                elif ev[0] == "passo":
                    self.progresso.step(1)
                elif ev[0] == "fim":
                    self.rodando = False
                    self.processo = None
                    self.bt_conv.configure(text="Converter")
                    self.bt_add.state(["!disabled"])
                    self.bt_rem.state(["!disabled"])
                    self.msg.set("Conversão cancelada." if self.cancelar else
                                 "Concluído! Dê dois cliques num arquivo para abrir o resultado.")
        except queue.Empty:
            pass
        self.raiz.after(100, self._processar_fila)


def main():
    raiz = TkinterDnD.Tk() if TEM_DND else tk.Tk()
    App(raiz)
    raiz.mainloop()


if __name__ == "__main__":
    main()
