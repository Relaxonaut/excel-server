# Excel-Server

Excel schickt den Inhalt eines benannten Bereichs per Power Query an den Server.
Der Server liest die Werte per Zelladresse (wie G20), rechnet alle aktiven Module
und antwortet mit einer Tabelle (Name / Wert).

## Lokal testen
    pip install -r requirements.txt
    uvicorn main:app --port 8000
    -> http://localhost:8000/

## Auf Render
1. Ordner in ein (privates) GitHub-Repo pushen.
2. Render: New -> Blueprint -> Repo waehlen (render.yaml wird erkannt).
3. Im Dashboard unter Environment stehen API_TOKEN und UI_PASSWORD.
   UI: https://<name>.onrender.com/  (Benutzername egal, Passwort = UI_PASSWORD)

## Neue Funktion
Datei in modules/ anlegen, von `Module` erben (Vorlage: modules/mwst.py), neu starten.
