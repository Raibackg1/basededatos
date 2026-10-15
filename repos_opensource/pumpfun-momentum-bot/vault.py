"""
Cofre criptografado das chaves sensiveis das carteiras.

Criptografia REAL: a senha NUNCA e salva. Ela deriva (PBKDF2-SHA256, 200k
iteracoes) a chave que cifra/decifra os segredos com Fernet (AES-128 + HMAC).
Se esquecer a senha e nao tiver backup, os dados sao perdidos — esse e o ponto.

Segredos guardados no cofre (secrets.vault):
    LIVE_SEED_PHRASE, HELIUS_API_KEY, PUMPPORTAL_API_KEY, LIVE_RPC

Os enderecos publicos (EXEC_WALLET, STREAM_WALLET) e os parametros TS_*
continuam no .env (nao sao segredo).

Uso:
    python vault.py        # abre a interface (criar cofre / abrir e editar)
"""
from __future__ import annotations

import base64
import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from dotenv import load_dotenv, unset_key

load_dotenv()

VAULT_FILE = Path(__file__).parent / "secrets.vault"
ENV_FILE = Path(__file__).parent / ".env"
ITERATIONS = 200_000

# chaves sensiveis que o cofre gerencia
SECRET_KEYS = ["LIVE_SEED_PHRASE", "HELIUS_API_KEY", "PUMPPORTAL_API_KEY", "LIVE_RPC"]

# ---- cripto -----------------------------------------------------------------

def _derive(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def exists() -> bool:
    return VAULT_FILE.exists()


def lock(password: str, data: dict):
    salt = os.urandom(16)
    token = Fernet(_derive(password, salt)).encrypt(json.dumps(data).encode("utf-8"))
    VAULT_FILE.write_text(json.dumps({
        "salt": base64.b64encode(salt).decode(),
        "token": token.decode(),
    }))


def unlock(password: str) -> dict:
    """Decifra o cofre. Levanta InvalidToken se a senha estiver errada."""
    blob = json.loads(VAULT_FILE.read_text())
    salt = base64.b64decode(blob["salt"])
    data = Fernet(_derive(password, salt)).decrypt(blob["token"].encode())
    return json.loads(data)


def unlock_into_env(password: str) -> dict:
    """Decifra e injeta os segredos no os.environ (so em memoria). Retorna o dict."""
    data = unlock(password)
    for k, v in data.items():
        if v:
            os.environ[k] = str(v)
    return data


def remove_secrets_from_env():
    """Apaga as chaves sensiveis do .env em texto puro (ativa a criptografia real)."""
    for k in SECRET_KEYS:
        try:
            unset_key(str(ENV_FILE), k)
        except Exception:
            pass


# ---- GUI --------------------------------------------------------------------

class VaultGUI:
    def __init__(self, root):
        self.root = root
        root.title("Cofre das Carteiras 🔒")
        root.geometry("680x520")
        self.password = None          # senha em memoria apos abrir
        self.fields = {}              # key -> StringVar
        self.body = ttk.Frame(root)
        self.body.pack(fill="both", expand=True, padx=12, pady=12)
        if exists():
            self._screen_unlock()
        else:
            self._screen_setup()

    def _clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    # ---- tela: abrir (cofre existe) --------------------------------------

    def _screen_unlock(self):
        self._clear()
        ttk.Label(self.body, text="Cofre protegido. Digite a senha:",
                  font=("Segoe UI", 11)).pack(pady=(20, 8))
        self.var_pw = tk.StringVar()
        e = ttk.Entry(self.body, textvariable=self.var_pw, show="•", width=32)
        e.pack(pady=4)
        e.focus()
        e.bind("<Return>", lambda _=None: self._do_unlock())
        ttk.Button(self.body, text="Abrir", command=self._do_unlock).pack(pady=8)
        self.lbl_msg = ttk.Label(self.body, text="", foreground="#b00")
        self.lbl_msg.pack()

    def _do_unlock(self):
        try:
            data = unlock(self.var_pw.get())
        except InvalidToken:
            self.lbl_msg.config(text="Senha errada.")
            return
        except Exception as e:
            self.lbl_msg.config(text=f"Erro: {e}")
            return
        self.password = self.var_pw.get()
        self._screen_edit(data)

    # ---- tela: criar cofre (nao existe) ----------------------------------

    def _screen_setup(self):
        self._clear()
        ttk.Label(self.body, text="Criar cofre — define uma senha forte.",
                  font=("Segoe UI", 11, "bold")).pack(pady=(8, 2))
        ttk.Label(self.body, text="A senha NAO e salva. Se esquecer e nao tiver backup, "
                  "os dados se perdem.", foreground="#a60", wraplength=620).pack(pady=(0, 10))
        # campos vem do .env atual
        grid = ttk.Frame(self.body)
        grid.pack(fill="x")
        for i, k in enumerate(SECRET_KEYS):
            ttk.Label(grid, text=k).grid(row=i, column=0, sticky="e", padx=(0, 6), pady=3)
            var = tk.StringVar(value=os.environ.get(k, ""))
            self.fields[k] = var
            ttk.Entry(grid, textvariable=var, width=64, show="•").grid(row=i, column=1, pady=3)
        pw = ttk.Frame(self.body)
        pw.pack(pady=10)
        self.var_pw1 = tk.StringVar()
        self.var_pw2 = tk.StringVar()
        ttk.Label(pw, text="Senha:").grid(row=0, column=0, sticky="e", padx=4)
        ttk.Entry(pw, textvariable=self.var_pw1, show="•", width=24).grid(row=0, column=1)
        ttk.Label(pw, text="Repetir:").grid(row=1, column=0, sticky="e", padx=4)
        ttk.Entry(pw, textvariable=self.var_pw2, show="•", width=24).grid(row=1, column=1)
        ttk.Button(self.body, text="Criar cofre", command=self._do_create).pack(pady=6)

    def _do_create(self):
        p1, p2 = self.var_pw1.get(), self.var_pw2.get()
        if len(p1) < 6:
            messagebox.showwarning("Senha", "Use ao menos 6 caracteres.")
            return
        if p1 != p2:
            messagebox.showwarning("Senha", "As senhas nao batem.")
            return
        data = {k: v.get() for k, v in self.fields.items()}
        lock(p1, data)
        self.password = p1
        if messagebox.askyesno("Ativar criptografia",
                "Cofre criado. Remover as chaves sensiveis do .env em texto puro agora?\n\n"
                "(Recomendado — a partir daqui o app pede a senha pra usar as chaves.)"):
            remove_secrets_from_env()
        messagebox.showinfo("Backup",
            "Dica: clique em 'Exportar backup' e guarde o arquivo OFFLINE "
            "(pen drive, cofre fisico). E sua unica recuperacao se esquecer a senha.")
        self._screen_edit(data)

    # ---- tela: ver / editar ----------------------------------------------

    def _screen_edit(self, data):
        self._clear()
        ttk.Label(self.body, text="Carteiras / chaves (cofre aberto)",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        # enderecos publicos (so leitura, vem do .env)
        info = ttk.LabelFrame(self.body, text="Enderecos publicos (.env)")
        info.pack(fill="x", pady=6)
        for i, k in enumerate(["EXEC_WALLET", "STREAM_WALLET"]):
            ttk.Label(info, text=k + ":").grid(row=i, column=0, sticky="e", padx=(8, 4), pady=2)
            ttk.Label(info, text=os.environ.get(k, "—")).grid(row=i, column=1, sticky="w")

        sec = ttk.LabelFrame(self.body, text="Segredos (cifrados)")
        sec.pack(fill="x", pady=6)
        self.fields = {}
        self.show_secrets = tk.BooleanVar(value=False)
        for i, k in enumerate(SECRET_KEYS):
            ttk.Label(sec, text=k).grid(row=i, column=0, sticky="e", padx=(8, 4), pady=3)
            var = tk.StringVar(value=str(data.get(k, "")))
            self.fields[k] = var
            ttk.Entry(sec, textvariable=var, width=64, show="•").grid(row=i, column=1, pady=3)
        ttk.Checkbutton(sec, text="mostrar", variable=self.show_secrets,
                        command=self._toggle_show).grid(row=len(SECRET_KEYS), column=1, sticky="w")
        self._sec_frame = sec

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=10)
        ttk.Button(bar, text="💾 Salvar", command=self._do_save).pack(side="left", padx=4)
        ttk.Button(bar, text="🔑 Trocar senha", command=self._do_change_pw).pack(side="left", padx=4)
        ttk.Button(bar, text="📤 Exportar backup", command=self._do_backup).pack(side="left", padx=4)
        ttk.Button(bar, text="Fechar", command=self.root.destroy).pack(side="right", padx=4)

    def _toggle_show(self):
        show = "" if self.show_secrets.get() else "•"
        for child in self._sec_frame.winfo_children():
            if isinstance(child, ttk.Entry):
                child.config(show=show)

    def _do_save(self):
        data = {k: v.get() for k, v in self.fields.items()}
        lock(self.password, data)
        messagebox.showinfo("Cofre", "Salvo (re-cifrado).")

    def _do_change_pw(self):
        win = tk.Toplevel(self.root)
        win.title("Trocar senha")
        v1, v2 = tk.StringVar(), tk.StringVar()
        ttk.Label(win, text="Nova senha:").grid(row=0, column=0, padx=6, pady=4, sticky="e")
        ttk.Entry(win, textvariable=v1, show="•", width=24).grid(row=0, column=1, padx=6)
        ttk.Label(win, text="Repetir:").grid(row=1, column=0, padx=6, pady=4, sticky="e")
        ttk.Entry(win, textvariable=v2, show="•", width=24).grid(row=1, column=1, padx=6)

        def apply():
            if len(v1.get()) < 6 or v1.get() != v2.get():
                messagebox.showwarning("Senha", "Min 6 caracteres e iguais.")
                return
            data = {k: var.get() for k, var in self.fields.items()}
            lock(v1.get(), data)
            self.password = v1.get()
            win.destroy()
            messagebox.showinfo("Cofre", "Senha trocada e cofre re-cifrado.")
        ttk.Button(win, text="Aplicar", command=apply).grid(row=2, column=0, columnspan=2, pady=8)

    def _do_backup(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", title="Exportar backup (texto puro — guarde OFFLINE)",
            filetypes=[("Texto", "*.txt")])
        if not path:
            return
        lines = ["# BACKUP DAS CHAVES — TEXTO PURO. Guarde OFFLINE e em segredo.", ""]
        for k, v in self.fields.items():
            lines.append(f"{k}={v.get()}")
        for k in ("EXEC_WALLET", "STREAM_WALLET"):
            lines.append(f"{k}={os.environ.get(k, '')}")
        Path(path).write_text("\n".join(lines), encoding="utf-8")
        messagebox.showinfo("Backup", "Backup salvo. Guarde-o offline — e sua recuperacao.")


def open_window(parent=None):
    """Abre o cofre. Com parent -> janela no mesmo processo (funciona em .exe).
    Sem parent -> janela propria com mainloop (uso standalone)."""
    if parent is None:
        root = tk.Tk()
        VaultGUI(root)
        root.mainloop()
    else:
        VaultGUI(tk.Toplevel(parent))


def main():
    open_window()


if __name__ == "__main__":
    main()
