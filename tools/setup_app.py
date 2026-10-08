"""Windows desktop entry point, also used as the frozen background worker."""
import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from desktop_setup import APP_VERSION, CONVERTERS, preflight, worker
from steam_installations import discover


def self_test(destination):
    import tkinter
    from setup_campaign import runtime_directory
    for name in CONVERTERS: importlib.import_module(name)
    runtime = runtime_directory()
    for name in ('campaign_menu.lua', 'retail_diagnostics.lua'):
        if not (runtime / name).is_file(): raise RuntimeError('Missing bundled runtime: ' + name)
    result = {'version': APP_VERSION, 'frozen': bool(getattr(sys, 'frozen', False)),
              'tk': tkinter.Tcl().eval('info patchlevel'), 'converter_modules': len(CONVERTERS),
              'runtime_files': 2, 'status': 'passed'}
    Path(destination).write_text(json.dumps(result, indent=2), encoding='utf-8')


def launch_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    class SetupApp:
        def __init__(self, root):
            self.root = root
            root.title('KingdomComeGlueMapper Setup')
            root.geometry('1060x850'); root.minsize(940, 760)
            root.configure(bg='#edf1f7')
            style = ttk.Style(root)
            style.theme_use('clam')
            style.configure('.', font=('Segoe UI', 10), background='#edf1f7', foreground='#19273b')
            style.configure('TEntry', fieldbackground='white', padding=6)
            style.configure('TCombobox', fieldbackground='white', padding=5)
            style.configure('TButton', padding=(12, 7))
            style.configure('Accent.TButton', background='#245baf', foreground='white', font=('Segoe UI', 10, 'bold'))
            style.map('Accent.TButton', background=[('active', '#184b96'), ('disabled', '#a0aec0')])
            style.configure('TProgressbar', background='#245baf', troughcolor='#dce4f0', borderwidth=0)
            header = tk.Frame(root, bg='#172c4b', padx=26, pady=18); header.pack(fill='x')
            tk.Label(header, text='KingdomComeGlueMapper', bg='#172c4b', fg='white', font=('Segoe UI', 23, 'bold')).pack(anchor='w')
            tk.Label(header, text=f'SETUP  {APP_VERSION}   /   KCD1 world in the KCD2 engine', bg='#172c4b', fg='#bbd0ef', font=('Segoe UI', 11)).pack(anchor='w', pady=(4, 0))
            body = ttk.Frame(root, padding=(24, 14)); body.pack(fill='both', expand=True)
            ttk.Label(body, text='Experimental world import • Requires your own installed copies of both games.', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
            ttk.Label(body, text='Installs the map and Play KDC1 menu entry. Quests, the original intro and campaign save validation are still unfinished.').pack(anchor='w', pady=(3, 14))
            self.kcd1, self.kcd2 = tk.StringVar(), tk.StringVar()
            self.workspace = tk.StringVar(value=str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'KingdomComeGlueMapper/builds'))
            self.package = tk.StringVar()
            self.action = tk.StringVar(value='build_install')
            self.busy = False; self.proc = None; self.job = None; self.reader = None; self.buffer = ''; self.terminal_event = False
            self.controls = []
            form = ttk.Frame(body); form.pack(fill='x'); form.columnconfigure(1, weight=1)
            for row, (label, variable) in enumerate((('KCD1 installation', self.kcd1), ('KCD2 installation', self.kcd2), ('Build folder', self.workspace))):
                ttk.Label(form, text=label, width=19).grid(row=row, column=0, sticky='w', pady=4)
                entry = ttk.Entry(form, textvariable=variable); entry.grid(row=row, column=1, sticky='ew', padx=(0, 8), pady=4)
                button = ttk.Button(form, text='Browse…', command=lambda v=variable: self.browse(v)); button.grid(row=row, column=2)
                self.controls.extend((entry, button))
            options = ttk.Frame(body); options.pack(fill='x', pady=(10, 4))
            for label, value in (('Build and install', 'build_install'), ('Build package only', 'build_only'), ('Install existing package', 'install')):
                radio = ttk.Radiobutton(options, text=label, variable=self.action, value=value, command=self.mode_changed)
                radio.pack(side='left', padx=(0, 22)); self.controls.append(radio)
            self.package_row = ttk.Frame(body); self.package_row.pack(fill='x', pady=(4, 4))
            ttk.Label(self.package_row, text='Existing package', width=19).pack(side='left')
            self.package_entry = ttk.Entry(self.package_row, textvariable=self.package); self.package_entry.pack(side='left', fill='x', expand=True, padx=(0, 8))
            self.package_browse = ttk.Button(self.package_row, text='Browse…', command=lambda: self.browse(self.package)); self.package_browse.pack(side='left')
            self.controls.extend((self.package_entry, self.package_browse))
            ttk.Label(body, text='Full builds need at least 60 GiB of temporary space. Existing GlueMapper installations are backed up before replacement.', foreground='#52637c').pack(anchor='w', pady=(6, 10))
            self.status = tk.StringVar(value='Detecting Steam installations…')
            ttk.Label(body, textvariable=self.status, wraplength=985, font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(0, 8))
            actions = ttk.Frame(body); actions.pack(fill='x')
            scan = ttk.Button(actions, text='Find Steam games', command=self.detect); scan.pack(side='left', padx=(0, 8))
            check = ttk.Button(actions, text='Check locations', command=self.check); check.pack(side='left', padx=(0, 8))
            self.start = ttk.Button(actions, text='Build and install', style='Accent.TButton', command=self.begin); self.start.pack(side='right')
            self.controls.extend((scan, check, self.start))
            ttk.Separator(body).pack(fill='x', pady=14)
            self.stage = tk.StringVar(value='Ready to set up'); self.detail = tk.StringVar(value='Progress counts completed stages; the current stage shows activity or measured file progress.')
            ttk.Label(body, textvariable=self.stage, font=('Segoe UI', 11, 'bold')).pack(anchor='w')
            self.overall = ttk.Progressbar(body, maximum=100); self.overall.pack(fill='x', pady=(7, 7))
            self.current = ttk.Progressbar(body, maximum=100); self.current.pack(fill='x')
            ttk.Label(body, textvariable=self.detail, wraplength=980, foreground='#52637c').pack(anchor='w', pady=(5, 8))
            logframe = ttk.Frame(body); logframe.pack(fill='both', expand=True)
            self.log = tk.Text(logframe, bg='#122035', fg='#d5e3f7', insertbackground='white', font=('Consolas', 10), wrap='word', height=9, relief='flat', padx=10, pady=8, state='disabled')
            self.log.pack(side='left', fill='both', expand=True)
            scrollbar = ttk.Scrollbar(logframe, command=self.log.yview); scrollbar.pack(side='right', fill='y'); self.log.configure(yscrollcommand=scrollbar.set)
            footer = ttk.Frame(body); footer.pack(fill='x', pady=(10, 0))
            self.cancel = ttk.Button(footer, text='Cancel after current stage', command=self.request_cancel, state='disabled'); self.cancel.pack(side='left')
            self.open_log = ttk.Button(footer, text='Open log', command=self.show_log, state='disabled'); self.open_log.pack(side='right', padx=(8, 0))
            self.open_output = ttk.Button(footer, text='Open output folder', command=self.show_output, state='disabled'); self.open_output.pack(side='right')
            self.elapsed = tk.StringVar(); ttk.Label(footer, textvariable=self.elapsed).pack(side='left', padx=12)
            self.mode_changed(); root.protocol('WM_DELETE_WINDOW', self.close); root.after(100, self.detect)

        def browse(self, variable):
            chosen = filedialog.askdirectory(parent=self.root, initialdir=variable.get() or str(Path.home()))
            if chosen: variable.set(chosen)

        def append(self, line):
            self.log.configure(state='normal'); self.log.insert('end', line + '\n'); self.log.see('end')
            if int(self.log.index('end-1c').split('.')[0]) > 3000: self.log.delete('1.0', '1000.0')
            self.log.configure(state='disabled')

        def mode_changed(self):
            labels = {'build_install': 'Build and install', 'build_only': 'Build package', 'install': 'Install package'}
            self.start.configure(text=labels[self.action.get()])
            state = 'normal' if self.action.get() == 'install' and not self.busy else 'disabled'
            self.package_entry.configure(state=state); self.package_browse.configure(state=state)

        def detect(self):
            found, warnings = discover()
            for key, variable in (('kcd1', self.kcd1), ('kcd2', self.kcd2)):
                if found[key]: variable.set(found[key][0]); self.append(f'{key.upper()}: {found[key][0]}')
            for warning in warnings: self.append(warning)
            self.status.set('Steam games found. Check the locations, then choose an operation.' if all(found.values()) else 'Select any missing game folder with Browse. Both installed games are required.')

        def config(self):
            if not all(v.get().strip() for v in (self.kcd1, self.kcd2, self.workspace)):
                raise ValueError('Select both games and a build folder.')
            return dict(kcd1=str(Path(self.kcd1.get()).resolve()), kcd2=str(Path(self.kcd2.get()).resolve()),
                        workspace=str(Path(self.workspace.get()).resolve()), action=self.action.get(), package=self.package.get())

        def check(self):
            try:
                message = preflight(self.config()); self.status.set('Ready. ' + message); self.append(message); return True
            except (OSError, ValueError, KeyError) as error:
                self.status.set(str(error)); self.append('Check: ' + str(error)); return False

        def begin(self):
            if self.busy or not self.check(): return
            try:
                config = self.config()
                self.job = Path(config['workspace']) / (time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
                self.job.mkdir(parents=True)
                path = self.job / 'job.json'; path.write_text(json.dumps(config, indent=2), encoding='utf-8')
                command = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).resolve())]
                self.proc = subprocess.Popen([*command, '--worker', str(path)], creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except OSError as error:
                self.status.set(str(error)); return
            self.busy = True; self.started = time.monotonic(); self.terminal_event = False; self.buffer = ''
            if self.reader: self.reader.close()
            self.reader = None
            for control in self.controls: control.configure(state='disabled')
            self.cancel.configure(state='normal'); self.open_log.configure(state='normal'); self.open_output.configure(state='normal')
            self.overall['value'] = 0; self.status.set('Setup is running. Detailed output is saved in the build folder.')
            self.append('Started: ' + str(self.job)); self.root.after(150, self.poll)

        def poll(self):
            eventfile = self.job / 'events.jsonl'
            if self.reader is None and eventfile.exists(): self.reader = eventfile.open(encoding='utf-8')
            if self.reader:
                self.buffer += self.reader.read()
                lines = self.buffer.split('\n'); self.buffer = lines.pop()
                for line in lines:
                    if line: self.event(json.loads(line))
            seconds = int(time.monotonic() - self.started)
            self.elapsed.set(f'{seconds // 60:02d}:{seconds % 60:02d} elapsed')
            code = self.proc.poll()
            if code is None:
                self.root.after(200, self.poll); return
            # Drain the last flushed records after process exit.
            if self.reader:
                rest = self.buffer + self.reader.read(); self.reader.close(); self.reader = None
                for line in rest.splitlines():
                    if line: self.event(json.loads(line))
            if not self.terminal_event:
                self.status.set(f'Setup stopped unexpectedly (exit {code}). See the saved log.'); self.append(self.status.get())
            self.busy = False; self.current.stop(); self.cancel.configure(state='disabled')
            for control in self.controls: control.configure(state='normal')
            self.mode_changed()

        def event(self, event):
            kind = event['kind']
            if kind == 'stage':
                self.stage.set(f'Stage {event["index"] + 1} of {event["total"]} — {event["label"]}')
                self.overall['value'] = 100 * event['index'] / event['total']
                self.current.stop(); self.current.configure(mode='indeterminate'); self.current.start(15)
                self.detail.set('Working. Large conversion stages can take several minutes.'); self.append(self.stage.get())
            elif kind == 'progress':
                if event['total']:
                    self.current.stop(); self.current.configure(mode='determinate'); self.current['value'] = 100 * event['current'] / event['total']
                    self.detail.set(f'{event["label"]}: {100 * event["current"] / event["total"]:.1f}%  •  {event["detail"]}')
            elif kind == 'log': self.append(event['message'])
            elif kind in ('done', 'error', 'cancelled'):
                self.terminal_event = True; self.status.set(event['message']); self.stage.set({'done': 'Setup complete', 'error': 'Setup needs attention', 'cancelled': 'Setup cancelled'}[kind])
                self.current.stop(); self.append(event['message'])
                if kind == 'done': self.overall['value'] = 100; self.current.configure(mode='determinate'); self.current['value'] = 100
                for key in ('package', 'installed', 'backup'):
                    if event.get(key): self.append(key.capitalize() + ': ' + event[key])
                if event.get('traceback'): self.append(event['traceback'])

        def request_cancel(self):
            (self.job / 'cancel.requested').touch()
            self.cancel.configure(state='disabled'); self.status.set('Cancellation requested. The current conversion stage will finish safely first.')

        def show_log(self):
            if self.job and (self.job / 'setup.log').exists(): os.startfile(self.job / 'setup.log')

        def show_output(self):
            if self.job: os.startfile(self.job)

        def close(self):
            if self.busy:
                messagebox.showinfo('Setup is running', 'Use Cancel after current stage, then wait for setup to finish before closing.', parent=self.root)
            else: self.root.destroy()

    root = tk.Tk(); SetupApp(root); root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=Path)
    parser.add_argument('--self-test', type=Path)
    args = parser.parse_args()
    if args.worker: return worker(args.worker)
    if args.self_test: self_test(args.self_test); return 0
    launch_gui(); return 0


if __name__ == '__main__':
    raise SystemExit(main())
