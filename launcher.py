"""Small Windows-friendly GUI for the complete parse-and-visualize workflow."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


class Launcher:
    def __init__(self, initial_input: str = "") -> None:
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = tk.Tk()
        self.root.title("CS2 Demo Parser")
        self.root.geometry("820x590")
        self.root.minsize(680, 500)

        self.input_path = tk.StringVar(value=initial_input)
        self.output_path = tk.StringVar(value=str(PROJECT_ROOT / "output"))
        self.open_browser = tk.BooleanVar(value=True)
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.running = False

        self._build()
        self.root.after(100, self._poll_messages)
        if initial_input:
            self.root.after(350, self.start)

    def _build(self) -> None:
        from tkinter import ttk

        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="CS2 Demo Parser", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text="Выберите RAR, DEM, DEM.ZST или папку — всё остальное выполнится автоматически.",
        ).pack(anchor="w", pady=(4, 22))

        ttk.Label(outer, text="Входные данные").pack(anchor="w")
        input_row = ttk.Frame(outer)
        input_row.pack(fill="x", pady=(5, 14))
        ttk.Entry(input_row, textvariable=self.input_path).pack(side="left", fill="x", expand=True)
        ttk.Button(input_row, text="Файл…", command=self._browse_file).pack(side="left", padx=(8, 0))
        ttk.Button(input_row, text="Папка…", command=self._browse_folder).pack(side="left", padx=(8, 0))

        ttk.Label(outer, text="Папка результата").pack(anchor="w")
        output_row = ttk.Frame(outer)
        output_row.pack(fill="x", pady=(5, 14))
        ttk.Entry(output_row, textvariable=self.output_path).pack(side="left", fill="x", expand=True)
        ttk.Button(output_row, text="Выбрать…", command=self._browse_output).pack(side="left", padx=(8, 0))

        options = ttk.Frame(outer)
        options.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(
            options,
            text="Открыть общую страницу после завершения",
            variable=self.open_browser,
        ).pack(side="left")
        self.start_button = ttk.Button(options, text="Запустить всё", command=self.start)
        self.start_button.pack(side="right")

        self.status = ttk.Label(outer, text="Готов к запуску")
        self.status.pack(anchor="w", pady=(0, 6))
        self.log = self.tk.Text(
            outer,
            height=18,
            wrap="word",
            state="disabled",
            font=("Consolas", 10),
            background="#0b1018",
            foreground="#d9e2f2",
            insertbackground="#ffffff",
        )
        self.log.pack(fill="both", expand=True)

    def _browse_file(self) -> None:
        from tkinter import filedialog

        selected = filedialog.askopenfilename(
            title="Выберите архив или демку",
            filetypes=[
                ("Поддерживаемые файлы", "*.rar *.dem *.zst"),
                ("RAR архив", "*.rar"),
                ("CS2 demo", "*.dem"),
                ("Zstandard demo", "*.zst"),
                ("Все файлы", "*.*"),
            ],
        )
        if selected:
            self.input_path.set(selected)

    def _browse_folder(self) -> None:
        from tkinter import filedialog

        selected = filedialog.askdirectory(title="Выберите папку с архивами или демками")
        if selected:
            self.input_path.set(selected)

    def _browse_output(self) -> None:
        from tkinter import filedialog

        selected = filedialog.askdirectory(title="Выберите папку результата")
        if selected:
            self.output_path.set(selected)

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self) -> None:
        from tkinter import messagebox

        if self.running:
            return
        source = Path(self.input_path.get().strip().strip('"')).expanduser()
        if not source.exists():
            messagebox.showerror("CS2 Demo Parser", "Выберите существующий файл или папку.")
            return
        output = Path(self.output_path.get().strip().strip('"')).expanduser()
        if not str(output):
            messagebox.showerror("CS2 Demo Parser", "Укажите папку результата.")
            return

        self.input_path.set(str(source.resolve()))
        self.output_path.set(str(output.resolve()))
        self.running = True
        self.start_button.configure(state="disabled")
        self.status.configure(text="Обработка запущена…")
        self._append_log("\n=== Новый запуск ===\n")
        threading.Thread(
            target=self._run,
            args=(source.resolve(), output.resolve()),
            daemon=True,
        ).start()

    def _run(self, source: Path, output: Path) -> None:
        command = [
            sys.executable,
            str(PROJECT_ROOT / "main.py"),
            str(source),
            "--mode",
            "parse-viz",
            "--out",
            str(output),
            "--no-serve",
            "--no-browser",
        ]
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self.process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                creationflags=creationflags,
            )
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.messages.put(("log", line))
            return_code = self.process.wait()
        except Exception as exc:  # pragma: no cover - OS/UI integration
            self.messages.put(("log", f"\n[!] Не удалось запустить обработку: {exc}\n"))
            return_code = 1
        finally:
            self.process = None
        self.messages.put(("done", (return_code, output)))

    def _poll_messages(self) -> None:
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "log":
                    self._append_log(str(value))
                elif kind == "done":
                    return_code, output = value  # type: ignore[misc]
                    self._finished(int(return_code), Path(output))
        except queue.Empty:
            pass
        self.root.after(100, self._poll_messages)

    def _finished(self, return_code: int, output: Path) -> None:
        from tkinter import messagebox

        self.start_button.configure(state="normal")
        self.running = False
        if return_code != 0:
            self.status.configure(text="Завершено с ошибкой")
            messagebox.showerror(
                "CS2 Demo Parser",
                "Не все демки удалось обработать. Подробности находятся в журнале.",
            )
            return

        self.status.configure(text="Готово")
        entrypoint = output / "index.html"
        if not entrypoint.is_file():
            dashboards = sorted(output.glob("*/visualization/index.html"))
            if dashboards:
                entrypoint = dashboards[0]
        if self.open_browser.get() and entrypoint.is_file():
            webbrowser.open(entrypoint.resolve().as_uri())
        messagebox.showinfo("CS2 Demo Parser", f"Обработка завершена.\n\nРезультат:\n{output}")

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(
            "usage: cs2-demo-launcher [RAR_OR_DEMO_OR_FOLDER]\n\n"
            "Open the graphical one-click launcher. If a path is supplied, "
            "processing starts automatically."
        )
        return 0
    if "--version" in sys.argv[1:]:
        from cs2parser import __version__

        print(f"cs2-demo-launcher {__version__}")
        return 0
    initial_input = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        Launcher(initial_input).run()
    except ImportError as exc:
        print(f"Tkinter недоступен: {exc}", file=sys.stderr)
        print("Запустите вместо GUI: python main.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
