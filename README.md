# sshserver

Applet GTK 3 per controllare il server OpenSSH dal tray di una sessione X11.
Funziona con un pannello che supporta XEmbed, anche senza AI-bar. Il sistema deve
usare systemd con le unità `ssh.service` e, quando disponibile, `ssh.socket`.

## Installazione

Su Debian, Ubuntu o Linux Mint, installa Python 3.10 o successivo e le dipendenze di sistema.
Per autorizzare le operazioni dal menu serve un agente Polkit nella sessione grafica.

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 openssh-server pkexec pipx
git clone https://github.com/vroby65/sshserver.git
cd sshserver
pipx install --python /usr/bin/python3 --system-site-packages .
sshserver
```

Se `sshserver` non è nel `PATH`, esegui `pipx ensurepath` e riapri il terminale.
Per provare l'applet dalla cartella del repository puoi anche usare `/usr/bin/python3 -m sshserver`.

## Uso

L'icona è verde quando il servizio SSH o il suo socket di attivazione è in
funzione. È grigia quando SSH è spento, assente o quando lo stato è indisponibile.
Fai clic sull'icona per aprire la finestra con **Avvia**, **Ferma** e i log
di OpenSSH. Il clic destro apre il menu rapido **Attiva** o **Disattiva**.
Polkit chiede l'autorizzazione per cambiare lo stato del servizio. L'arresto
ferma anche il socket, quando presente. Le impostazioni di avvio di systemd
restano come configurate.

La finestra mostra gli ultimi 200 eventi del journal, dai più recenti. Puoi
cercare un utente, un IP o un testo e limitare il risultato ad accessi riusciti,
tentativi falliti oppure connessioni e sessioni. La lettura usa i permessi del
tuo utente sul journal e si aggiorna ogni cinque secondi mentre la finestra
è aperta. OpenSSH registra le attività previste dalla configurazione del server.
La registrazione dei comandi dentro una sessione richiede un audit dedicato.

Per chiudere l'applet avviata in primo piano, premi Ctrl+C nel terminale.

## Integrazione con AI-bar

AI-bar installa `sshserver` come dipendenza e lo avvia come processo separato.
Imposta le dimensioni dell'icona con `--icon-size` e usa `--watch-stdin` per
chiudere l'applet quando il pannello chiude la pipe di supervisione.

## Test

Con `xvfb` e `xauth` installati, esegui i test in un display isolato.

```bash
xvfb-run -a /usr/bin/python3 -m unittest discover -s tests
```

Il progetto deriva dall'applet di [AI-bar](https://github.com/vroby65/ai-bar/blob/4cb767260dda25323951696e29ca211d8e186e49/ai_bar/ssh_server.py) ed è distribuito con licenza MIT.
