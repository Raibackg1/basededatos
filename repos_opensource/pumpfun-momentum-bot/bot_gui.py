"""
Interface grafica do bot de momentum (token_stream.py).

- Ajusta TODOS os parametros do bot
- Alterna entre modo SIMULADO e REAL (live)
- Mostra o saldo das carteiras (execucao + stream), lidos do .env
- Acompanha estatisticas, posicoes abertas e log em tempo real

Os enderecos das carteiras vem do .env (EXEC_WALLET, STREAM_WALLET); as
chaves (LIVE_SEED_PHRASE, PUMPPORTAL_API_KEY) tambem ficam la.

Uso:
    python bot_gui.py
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import requests
from dotenv import load_dotenv

import token_stream as ts

load_dotenv()

RPC = os.environ.get("LIVE_RPC", "")
EXEC_WALLET = os.environ.get("EXEC_WALLET", "")
STREAM_WALLET = os.environ.get("STREAM_WALLET", "")
PRESET_DIR = Path(__file__).parent / "presets"
PRESET_DIR.mkdir(exist_ok=True)

RECHARGE_SOL = 0.02       # quanto cada recarga transfere pra carteira do stream
AUTO_THRESHOLD = 0.025    # no modo auto, recarrega quando o stream cai abaixo disso

# (label, nome do global em token_stream, tipo, dica)  — agrupados por secao
PARAM_GROUPS = {
    "Entrada": [
        ("Gatilho (+%)", "ENTRY_TRIGGER_PCT", float,
         "So entra quando o mcap sobe esse % acima do primeiro preco visto. Maior = mais seletivo, menos entradas."),
        ("Janela (s)", "ENTRY_WINDOW_SEC", int,
         "A subida do gatilho tem que acontecer dentro desse tempo a partir do nascimento do token."),
        ("Min. buyers", "MIN_BUYERS", int,
         "Compradores unicos minimos pra considerar entrada. Filtra token de 1 pessoa so (rug)."),
        ("Idade max. (s)", "MAX_AGE_ENTER_SEC", int,
         "Idade maxima do token pra ainda entrar. Evita pegar token velho que ja perdeu o momentum."),
        ("Anti-dump (%)", "ANTI_DUMP_PCT", float,
         "Descarta o token se ele cair esse % do preco de referencia ANTES de disparar o gatilho."),
        ("Corte: checa em (s)", "EARLY_CHECK_SEC", int,
         "Momento em que verifica se o token tem tracao minima (corte precoce)."),
        ("Corte: min buyers", "EARLY_MIN_BUYERS", int,
         "Se na checagem precoce tiver menos buyers que isso, descarta cedo — economiza custo do stream."),
    ],
    "Saida": [
        ("Stop duro (-%)", "HARD_STOP_PCT", float,
         "Vende se a posicao cair esse % da entrada. Rede de seguranca pra cortar os que nao sobem."),
        ("Trailing ativa (+%)", "TRAIL_ACTIVATE_PCT", float,
         "So comeca a seguir o topo depois de ja estar esse % no lucro (evita sair no chiado perto da entrada)."),
        ("Trailing queda (-%)", "TRAIL_DROP_PCT", float,
         "Depois de ativado, vende quando cair esse % a partir do PICO. E a saida por reversao."),
        ("Inatividade (s)", "INACTIVITY_SEC", int,
         "Vende se o token ficar esse tempo sem nenhum trade (token morto / sem liquidez)."),
    ],
    "Carteira / risco": [
        ("Stake (SOL)", "STAKE_SOL", float,
         "Tamanho fixo de cada posicao, em SOL."),
        ("Max pos. (sim)", "MAX_POSITIONS", int,
         "Maximo de posicoes simultaneas no modo SIMULADO."),
        ("Max pos. (real)", "LIVE_MAX_POSITIONS", int,
         "Maximo de posicoes simultaneas no modo REAL. Exposicao maxima = isso x stake."),
        ("Watch max", "MAX_WATCH", int,
         "Quantos tokens acompanhar ao mesmo tempo. Mais = mais carga e custo de stream."),
        ("Disjuntor (-SOL)", "MAX_LOSS_SOL", float,
         "No modo REAL: para tudo e fecha as posicoes apos perder esse total realizado (em SOL)."),
    ],
    "Atrito (so simulado)": [
        ("Taxa/lado (%)", "FEE_PCT_PER_SIDE", float,
         "Taxa estimada por lado (pump.fun + PumpPortal). Usada SO no PnL simulado, nao afeta o real."),
        ("Slip entrada (%)", "ENTRY_SLIP_PCT", float,
         "Slippage+latencia assumido na compra (voce preenche acima do preco). So no simulado."),
        ("Slip saida (%)", "EXIT_SLIP_PCT", float,
         "Slippage assumido na venda (voce recebe abaixo do preco). So no simulado."),
        ("Custo fixo (SOL)", "FIXED_COST_SOL", float,
         "Custo fixo por trade (priority fee + dust de ATA). So no simulado."),
    ],
}


def short(s, n=8):
    return (s[:n] + "…") if s and len(s) > n else (s or "")


class Tooltip:
    """Dica que aparece ao passar o mouse sobre um widget."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, wraplength=340,
                 font=("Segoe UI", 9)).pack(ipadx=5, ipady=3)

    def _hide(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class BotGUI:
    def __init__(self, root):
        self.root = root
        root.title("Bot de Momentum — pump.fun")
        root.geometry("1040x860")

        self.bot = None
        self.bot_thread = None
        self.log_q = queue.Queue()
        self.bal_q = queue.Queue()
        self.vars = {}          # nome -> StringVar
        self._bal_counter = 0
        self._wallet_executor = None  # LiveExecutor avulso pra transferencias
        self._recharging = False
        self._stream_bal = None       # ultimo saldo numerico do stream

        self._build_config()
        self._build_controls()
        self._build_wallets()
        self._build_stats()
        self._build_holdings()
        self._build_log()

        self._refresh_balances()
        self._tick()

    # ---- construcao da UI -------------------------------------------------

    def _build_config(self):
        outer = ttk.Frame(self.root)
        outer.pack(fill="x", padx=8, pady=4)
        for col, (sec, fields) in enumerate(PARAM_GROUPS.items()):
            f = ttk.LabelFrame(outer, text=sec)
            f.grid(row=0, column=col, sticky="nsew", padx=4)
            outer.columnconfigure(col, weight=1)
            for i, (label, name, _typ, tip) in enumerate(fields):
                lbl = ttk.Label(f, text=label)
                lbl.grid(row=i, column=0, sticky="e", padx=(6, 2), pady=2)
                default = getattr(ts, name)
                var = tk.StringVar(value=self._fmt(default))
                self.vars[name] = var
                ent = ttk.Entry(f, textvariable=var, width=9)
                ent.grid(row=i, column=1, padx=(0, 6), pady=2)
                Tooltip(lbl, tip)   # dica no rotulo e no campo
                Tooltip(ent, tip)

    def _build_controls(self):
        f = ttk.Frame(self.root)
        f.pack(fill="x", padx=8, pady=4)
        self.var_live = tk.BooleanVar(value=False)
        self.chk_live = ttk.Checkbutton(f, text="MODO REAL (live) — dinheiro de verdade",
                                        variable=self.var_live, command=self._on_toggle_live)
        self.chk_live.pack(side="left", padx=4)
        self.lbl_live = ttk.Label(f, text="", foreground="#b00")
        self.lbl_live.pack(side="left", padx=8)
        self.btn_stop = ttk.Button(f, text="■ Parar", command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="right", padx=4)
        self.btn_start = ttk.Button(f, text="▶ Iniciar", command=self.on_start)
        self.btn_start.pack(side="right", padx=4)
        ttk.Separator(f, orient="vertical").pack(side="right", fill="y", padx=8)
        ttk.Button(f, text="📂 Carregar", command=self._load_preset).pack(side="right", padx=2)
        ttk.Button(f, text="💾 Salvar", command=self._save_preset).pack(side="right", padx=2)

    def _build_wallets(self):
        f = ttk.LabelFrame(self.root, text="Carteiras")
        f.pack(fill="x", padx=8, pady=4)
        self.var_exec_bal = tk.StringVar(value="—")
        self.var_stream_bal = tk.StringVar(value="—")
        ttk.Label(f, text=f"Execucao ({short(EXEC_WALLET, 6)}):").grid(row=0, column=0, sticky="e", padx=(8, 2), pady=4)
        ttk.Label(f, textvariable=self.var_exec_bal, font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w", padx=(0, 16))
        ttk.Label(f, text=f"Stream ({short(STREAM_WALLET, 6)}):").grid(row=0, column=2, sticky="e", padx=(8, 2), pady=4)
        ttk.Label(f, textvariable=self.var_stream_bal, font=("Segoe UI", 10, "bold")).grid(row=0, column=3, sticky="w", padx=(0, 16))
        ttk.Button(f, text="↻ Atualizar saldos", command=self._refresh_balances).grid(row=0, column=4, padx=8)
        ttk.Button(f, text="🔑 Carteiras", command=self._open_vault).grid(row=0, column=5, padx=8)

        # recarga do stream a partir da carteira de execucao
        self.var_auto_recharge = tk.BooleanVar(value=False)
        self.btn_recharge = ttk.Button(f, text=f"⛽ Recarregar stream (+{RECHARGE_SOL})",
                                       command=self._recharge_stream)
        self.btn_recharge.grid(row=1, column=2, columnspan=2, sticky="w", padx=8, pady=(0, 6))
        ttk.Checkbutton(f, text=f"auto (quando < {AUTO_THRESHOLD})", variable=self.var_auto_recharge)\
            .grid(row=1, column=4, sticky="w", pady=(0, 6))

    def _build_stats(self):
        f = ttk.LabelFrame(self.root, text="Estatisticas")
        f.pack(fill="x", padx=8, pady=4)
        labels = [
            ("Modo", "mode"), ("Vistos", "n_seen"), ("Entrou", "n_entered"),
            ("Abertas", "n_holding"), ("Observando", "n_watching"),
            ("Fechados", "n_closed"), ("Win %", "win_rate"), ("PnL", "pnl"),
        ]
        self.stat_vars = {}
        for i, (label, key) in enumerate(labels):
            ttk.Label(f, text=label + ":").grid(row=0, column=i * 2, sticky="e", padx=(8, 2), pady=6)
            v = tk.StringVar(value="—")
            self.stat_vars[key] = v
            ttk.Label(f, textvariable=v, font=("Segoe UI", 10, "bold")).grid(row=0, column=i * 2 + 1, sticky="w", padx=(0, 10))

    def _build_holdings(self):
        f = ttk.LabelFrame(self.root, text="Posicoes abertas")
        f.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("name", "mint", "ret", "peak")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=7)
        for c, txt, w in [("name", "Token", 220), ("mint", "Mint", 220),
                          ("ret", "Retorno", 120), ("peak", "Pico", 120)]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=6, pady=6)

    def _build_log(self):
        f = ttk.LabelFrame(self.root, text="Log")
        f.pack(fill="both", expand=True, padx=8, pady=4)
        self.txt = tk.Text(f, height=10, state="disabled", wrap="word")
        self.txt.pack(fill="both", expand=True, padx=6, pady=6)

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _fmt(v):
        if isinstance(v, float):
            return f"{v:g}"
        return str(v)

    def _collect_params(self):
        params = {}
        for sec, fields in PARAM_GROUPS.items():
            for label, name, typ, _tip in fields:
                raw = self.vars[name].get().strip()
                params[name] = typ(raw)
        params["LIVE"] = bool(self.var_live.get())
        return params

    def _log(self, line):
        self.txt.config(state="normal")
        self.txt.insert(tk.END, line + "\n")
        self.txt.see(tk.END)
        self.txt.config(state="disabled")

    # ---- presets ----------------------------------------------------------

    def _save_preset(self):
        path = filedialog.asksaveasfilename(
            initialdir=PRESET_DIR, defaultextension=".json",
            filetypes=[("Preset", "*.json")], title="Salvar preset")
        if not path:
            return
        data = {
            "params": {name: var.get() for name, var in self.vars.items()},
            "live": bool(self.var_live.get()),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            messagebox.showerror("Salvar", f"Nao consegui salvar: {e}")
            return
        self._log(f"preset salvo: {os.path.basename(path)}")

    def _load_preset(self):
        path = filedialog.askopenfilename(
            initialdir=PRESET_DIR, filetypes=[("Preset", "*.json")],
            title="Carregar preset")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            messagebox.showerror("Carregar", f"Nao consegui ler: {e}")
            return
        for name, val in (data.get("params") or {}).items():
            if name in self.vars:
                self.vars[name].set(str(val))
        self.var_live.set(bool(data.get("live", False)))
        self._on_toggle_live()
        self._log(f"preset carregado: {os.path.basename(path)}"
                  + (" — Reinicie o bot pra aplicar." if self.bot_thread and self.bot_thread.is_alive() else ""))

    # ---- saldo das carteiras ---------------------------------------------

    def _open_vault(self):
        """Abre o cofre das carteiras no mesmo processo (funciona como .py e como .exe)."""
        try:
            import vault
            vault.open_window(self.root)
        except Exception as e:
            messagebox.showerror("Carteiras", f"Nao consegui abrir o cofre: {e}")

    def _get_executor(self):
        """Reusa o executor do bot (se em live) ou cria um avulso pra transferencias."""
        if self.bot and self.bot.executor:
            return self.bot.executor
        if self._wallet_executor is None:
            from live_executor import LiveExecutor
            self._wallet_executor = LiveExecutor()
        return self._wallet_executor

    def _recharge_stream(self):
        if self._recharging:
            return
        if not STREAM_WALLET:
            messagebox.showerror("Recarga", "STREAM_WALLET nao definido no .env.")
            return
        self._recharging = True
        self.btn_recharge.config(state="disabled", text="recarregando…")

        def work():
            try:
                ex = self._get_executor()
                sig = ex.transfer_sol(STREAM_WALLET, RECHARGE_SOL)
                self.log_q.put(f"[recarga] +{RECHARGE_SOL} SOL -> stream (sig {sig[:10]}…)")
            except Exception as e:
                self.log_q.put(f"[recarga falhou] {str(e)[:90]}")
            finally:
                self._recharging = False
                self.root.after(0, lambda: self.btn_recharge.config(
                    state="normal", text=f"⛽ Recarregar stream (+{RECHARGE_SOL})"))
                time.sleep(2)
                self._refresh_balances()

        threading.Thread(target=work, daemon=True).start()

    def _refresh_balances(self):
        def work():
            out = {}
            for key, addr in [("exec", EXEC_WALLET), ("stream", STREAM_WALLET)]:
                if not addr or not RPC:
                    out[key] = None
                    continue
                try:
                    r = requests.post(RPC, json={"jsonrpc": "2.0", "id": 1,
                                                 "method": "getBalance", "params": [addr]}, timeout=10).json()
                    out[key] = r["result"]["value"] / 1e9
                except Exception:
                    out[key] = None
            self.bal_q.put(out)
        threading.Thread(target=work, daemon=True).start()

    # ---- modo real --------------------------------------------------------

    def _on_toggle_live(self):
        if self.var_live.get():
            self.lbl_live.config(text="⚠ vai operar com DINHEIRO REAL")
        else:
            self.lbl_live.config(text="")

    # ---- start / stop -----------------------------------------------------

    def on_start(self):
        if self.bot_thread and self.bot_thread.is_alive():
            return
        try:
            params = self._collect_params()
        except ValueError:
            messagebox.showerror("Parametros", "Tem valor invalido em algum campo.")
            return

        if params["LIVE"]:
            if not messagebox.askyesno("Confirmar modo REAL",
                    f"Vai operar com DINHEIRO REAL.\nStake {params['STAKE_SOL']} SOL · "
                    f"max {params['LIVE_MAX_POSITIONS']} pos · disjuntor -{params['MAX_LOSS_SOL']} SOL.\n\nContinuar?"):
                return

        ts.configure(params)
        try:
            self.bot = ts.TokenStreamSim(log_callback=self.log_q.put)
        except Exception as e:
            messagebox.showerror("Erro ao iniciar", str(e))
            return

        self.bot_thread = threading.Thread(target=lambda: asyncio.run(self.bot.run()), daemon=True)
        self.bot_thread.start()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self._set_config_state("disabled")

    def on_stop(self):
        if self.bot:
            self.bot.request_stop()
            self.btn_stop.config(state="disabled", text="parando…")

    def _set_config_state(self, state):
        for name, var in self.vars.items():
            pass  # entries ficam editaveis; so travamos via re-leitura no start
        self.chk_live.config(state=state)

    # ---- loop de atualizacao ----------------------------------------------

    def _tick(self):
        # log
        while not self.log_q.empty():
            self._log(self.log_q.get_nowait())
        # saldos
        while not self.bal_q.empty():
            b = self.bal_q.get_nowait()
            if b.get("exec") is not None:
                self.var_exec_bal.set(f"{b['exec']:.4f} SOL")
            if b.get("stream") is not None:
                s = b["stream"]
                self._stream_bal = s
                tag = "OK" if s >= 0.02 else "PARADO <0.02"
                self.var_stream_bal.set(f"{s:.4f} SOL ({tag})")

        # recarga automatica do stream
        if (self.var_auto_recharge.get() and not self._recharging
                and self._stream_bal is not None and self._stream_bal < AUTO_THRESHOLD):
            self._log(f"auto-recarga: stream em {self._stream_bal:.4f} < {AUTO_THRESHOLD}")
            self._recharge_stream()

        # estado do bot
        if self.bot:
            st = self.bot.get_state()
            self.stat_vars["mode"].set("REAL" if st["live"] else "simulado")
            self.stat_vars["n_seen"].set(str(st["n_seen"]))
            self.stat_vars["n_entered"].set(str(st["n_entered"]))
            self.stat_vars["n_holding"].set(str(st["n_holding"]))
            self.stat_vars["n_watching"].set(str(st["n_watching"]))
            self.stat_vars["n_closed"].set(str(st["n_closed"]))
            self.stat_vars["win_rate"].set(f"{st['win_rate']:.0f}%")
            if st["live"]:
                pnl = f"{st['realized_pnl']:+.4f} SOL"
                if st["halted"]:
                    pnl += " [DISJUNTOR]"
            else:
                pnl = f"bruto {st['pnl_gross']:+.3f} / liq {st['pnl_net']:+.3f}"
            self.stat_vars["pnl"].set(pnl)

            self.tree.delete(*self.tree.get_children())
            for p in st["holding"]:
                self.tree.insert("", tk.END, values=(
                    short(p["name"], 28), short(p["mint"], 12),
                    f"{p['ret_pct']:+.0f}%", f"{p['peak_pct']:+.0f}%"))

        # detecta bot encerrado -> libera botoes
        if self.bot_thread and not self.bot_thread.is_alive() and str(self.btn_start["state"]) == "disabled":
            self.btn_start.config(state="normal")
            self.btn_stop.config(state="disabled", text="■ Parar")
            self.chk_live.config(state="normal")

        # recarrega saldos a cada ~15s
        self._bal_counter += 1
        if self._bal_counter >= 15:
            self._bal_counter = 0
            self._refresh_balances()

        self.root.after(1000, self._tick)


def _startup_unlock(root):
    """Se houver cofre e os segredos nao estiverem no env, pede a senha e injeta."""
    try:
        import vault
    except Exception:
        return
    if not vault.exists():
        return
    # ja disponiveis (cofre nao adotado, ou .env ainda tem) -> nao precisa
    if os.environ.get("PUMPPORTAL_API_KEY") and os.environ.get("LIVE_RPC"):
        return
    for _ in range(3):
        pw = simpledialog.askstring("Cofre 🔒", "Senha do cofre (Cancelar = modo limitado):",
                                    show="*", parent=root)
        if not pw:
            return
        try:
            vault.unlock_into_env(pw)
            return
        except Exception:
            messagebox.showerror("Cofre", "Senha errada.", parent=root)


def main():
    root = tk.Tk()
    root.withdraw()
    _startup_unlock(root)
    # re-le config do ambiente (caso o cofre tenha injetado os segredos agora)
    global RPC, EXEC_WALLET, STREAM_WALLET
    RPC = os.environ.get("LIVE_RPC", "")
    EXEC_WALLET = os.environ.get("EXEC_WALLET", "")
    STREAM_WALLET = os.environ.get("STREAM_WALLET", "")
    ts.refresh_env()
    root.deiconify()
    BotGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
