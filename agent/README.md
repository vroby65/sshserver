# Guida al progetto

L'applet usa GTK 3 e XEmbed su X11 e gestisce le unità OpenSSH di systemd.
`sshserver.py` contiene l'interfaccia, i comandi systemd e `main`, condiviso
da `python3 -m sshserver` e dal comando definito in `pyproject.toml`.
`README.md` documenta i requisiti, l'installazione e l'uso.

`SSHServerTray` aggiorna lo stato in un thread di lavoro e passa i risultati
alla UI tramite `GLib.idle_add`. Mantieni un solo lavoro in corso e rimuovi il
timer alla chiusura. Considera il socket di attivazione nel calcolo dello
stato e fermalo insieme al servizio. Le modifiche di stato passano da `pkexec`
e riguardano solo le due unità SSH gestite dall'applet.
`SSHServerWindow` avvia la lettura asincrona del journal quando è visibile.
I filtri operano sulle righe già lette. `journalctl` usa i permessi dell'utente
e il testo dei log viene inserito nella vista senza markup.
L'opzione `--watch-stdin` collega la durata dell'applet alla pipe aperta dal
pannello. L'avvio autonomo usa i segnali del processo per la chiusura.

Aggiorna `tests/test_ssh_server.py` per stato, menu, log e filtri.
Usa `tests/test_entrypoint.py` per le modifiche all'avvio. I test simulano
`systemctl` e `pkexec` per lasciare intatto il servizio SSH della macchina.

```bash
xvfb-run -a /usr/bin/python3 -m unittest discover -s tests
/usr/bin/python3 -m compileall -q sshserver.py tests
git diff --check
```

Prima della consegna verifica i test, la compilazione e il diff. Per cambiamenti
grafici controlla anche l'icona in un tray XEmbed. Aggiorna questa guida e il
README quando cambiano requisiti, avvio, installazione o verifiche.
